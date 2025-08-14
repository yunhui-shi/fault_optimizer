from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from networkx import max_flow_min_cost
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from schema import *
import asyncio,json
from typing import Literal
# 从另一个文件导入求解器函数
from optimization_solver import solve_dynamic_recovery_model
# 导入agent执行器
from agent import agent_executor
import logging,os
# 导入模糊匹配库
from fuzzywuzzy import fuzz, process
import requests
from datetime import datetime, timedelta
import pandapower as pp
from power_flow import create_network, run_powerflow
from area_topology import find_islands_without_high_voltage_buses, identify_interconnection_substations
from dotenv import load_dotenv
from collections import defaultdict
import base64
from kafka import KafkaProducer
load_dotenv(".env.example")
for key,value in os.environ.items():
    logging.info(f"{key}:{value}") #print to stdout

from fastapi.openapi.docs import (
    get_swagger_ui_html,
)

from fastapi.staticfiles import StaticFiles

# --- 数据模型 ---
class ChatRequest(BaseModel):
    message: str
    
class ChatResponse(BaseModel):
    response: str
    success: bool
    error: str = None

class PushTicketResponse(BaseModel):
    success: bool
    message: str
    error: str = None

def push_ticket_to_kafka():
    """
    将操作票文件转换为base64并推送到Kafka
    """
    try:
        # 检查操作票文件是否存在
        ticket_file_path = "result/temp_ops_ticket.xls"
        if not os.path.exists(ticket_file_path):
            return {"success": False, "message": "操作票文件不存在", "error": "File not found"}
        
        # 读取文件并转换为base64
        with open(ticket_file_path, "rb") as file:
            file_content = file.read()
            base64_content = base64.b64encode(file_content).decode('utf-8')
        
        # 创建Kafka生产者
        producer = KafkaProducer(
            bootstrap_servers=['10.33.66.38:9092'],
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        # 准备消息数据
        message_data = {
            "timestamp": datetime.now().isoformat(),
            "file_name": "temp_ops_ticket.xls",
            "file_content": base64_content,
            "content_type": "application/vnd.ms-excel"
        }
        
        # 发送消息到Kafka
        future = producer.send('operation_tickets', value=message_data)
        result = future.get(timeout=10)  # 等待发送完成
        
        producer.close()
        
        return {
            "success": True, 
            "message": f"操作票已成功推送到Kafka，分区: {result.partition}, 偏移量: {result.offset}"
        }
        
    except Exception as e:
        return {
            "success": False, 
            "message": "推送操作票到Kafka失败", 
            "error": str(e)
        }

async def event_stream(agent_executor, agent_input: str):
    async for event in agent_executor.astream_events({"input": agent_input},version="v1"):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            content = event["data"]["chunk"]
            if content:
                # Ensure content is a string, Langchain sometimes yields AIMessageChunk
                if hasattr(content, 'content'):
                    yield f"data: {json.dumps({'type': 'chunk', 'content': content.content})}\n\n"
                elif isinstance(content, str):
                    yield f"data: {json.dumps({'type': 'chunk', 'content': content})}\n\n"
        elif kind == "on_tool_start":
            yield f"data: {json.dumps({'type': 'tool_start', 'name': event['name'], 'input': event['data'].get('input')})}\n\n"
        elif kind == "on_tool_end":
            yield f"data: {json.dumps({'type': 'tool_end', 'name': event['name'], 'output': event['data'].get('output')})}\n\n"
        elif kind == "on_chain_end" or kind == "on_agent_end": # Langchain can use on_agent_end or on_chain_end for the final output
            output_data = event["data"].get("output", {})
            final_output = output_data.get("output") if isinstance(output_data, dict) else output_data
            if final_output:
                 yield f"data: {json.dumps({'type': 'final_output', 'content': str(final_output)})}\n\n"
        await asyncio.sleep(0.01) # Small delay to allow other tasks to run
    yield f"data: {json.dumps({'type': 'stream_end'})}\n\n" # Signal stream end


# --- FastAPI 应用实例 ---
app = FastAPI(
    title="500kV主变N-1负荷转供",
    description="一个通过API接口解决基于连通性推断的电网负荷转移优化问题的服务。该版本支持为开关动作指定不同成本。",
    version="1.1.0",
    docs_url=None,
    debug=True
)
from pathlib import Path
static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
# --- API 端点定义 ---
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
    )

@app.get("/chat-ui", include_in_schema=False)
async def chat_ui():
    """
    聊天界面
    """
    with open("static/chat.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)

@app.post("/solve/topology-optimization-with-cost", tags=["Optimization"])
def run_optimization_with_cost(data: OptimizationInput):
    """
    接收电网参数（包含开关操作成本）并执行拓扑优化。

    - **接收**: 一个包含电网所有参数和开关成本的JSON对象。
    - **执行**: 运行PySCIPOpt求解器找到最小化**总操作成本**的方案。
    - **返回**: 包含优化结果的JSON对象，如开关操作、最终负荷等。
    """
    try:
        params = data.model_dump()
        result = solve_dynamic_recovery_model(**params)

        if not result:
            raise HTTPException(
                status_code=422, 
                detail="求解器未能找到最优解或输入数据有问题。"
            )
            
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
def chat_with_agent(request: ChatRequest):
    """
    与智能代理进行对话交互。
    
    - **接收**: 用户的聊天消息
    - **处理**: 通过LangChain代理处理用户输入
    - **返回**: 代理的响应结果
    
    支持的功能:
    - 故障恢复优化（输入格式："设备名称故障/newfaultactivated"）
    - 优化配置修改（如："某某机组无法启动"、"某某线路不可用"等）
    - 一般性对话和咨询
    """
    try:
        # 调用agent执行器处理用户输入
        return StreamingResponse(event_stream(agent_executor, request.message + "/nothink"), media_type="text/event-stream")
        
    except Exception as e:
        return ChatResponse(
            response="",
            success=False,
            error=f"处理请求时发生错误: {str(e)}"
        )

@app.post("/push-ticket", response_model=PushTicketResponse, tags=["Operations"])
def push_operation_ticket():
    """
    推送操作票到Kafka消息队列
    """
    result = push_ticket_to_kafka()
    return PushTicketResponse(
        success=result["success"],
        message=result["message"],
        error=result.get("error")
    )
        
@app.get("/get_optimization_boundary", tags=["Optimization"])
def get_optimization_boundary(device_name: str, device_type: Literal["线路", "母线", "主变"],load_enlarge_factor: float):
    """
    根据设备名称和类型获取对应供区的优化边界数据
    
    - **device_name**: 设备名称，例如"双龙变1号主变"
    - **device_type**: 设备类型，可选值为"线路"、"母线"、"主变"
    - factor: 负荷放大系数
    - **返回**: 包含该设备所在供区数据的OptimizationInput对象
    """
    try:
        # 导入update_network模块
        # from update_network import update_network_for_small_areas
        
        # 读取area_statistics.json文件
        with open("result/area_statistics.json", "r", encoding="utf-8") as f:
            area_stats = json.load(f)
        
        # 先收集所有设备及其所属供区
        all_devices = {}
        
        if device_type == "主变":
            # 收集所有主变及其所属供区
            for area_name, area_data in area_stats.items():
                for transformer in area_data.get("主变", []):
                    all_devices[transformer.get("name", "")] = area_name
        else:
            raise HTTPException(
                status_code=400,
                detail=f"设备类型 {device_type} 暂不支持"
            )
        # 使用process.extractOne找到最相似的设备，并直接获取其所属供区
        best_match, score = process.extractOne(
            device_name, 
            all_devices.keys(), 
            scorer=fuzz.partial_ratio
        )
        print(best_match)
        # 直接从匹配结果获取供区
        target_area = all_devices[best_match] if best_match else None
        
        # # 更新网络配置（当供区主变数量≤2时）
        # logging.info(f"检查供区 {target_area} 是否需要更新网络配置...")
        # update_network_for_small_areas(target_area)
        
        # 如果没有找到对应的供区，返回错误
        if not target_area:
            raise HTTPException(
                status_code=404,
                detail=f"未找到设备 {device_name} ({device_type}) 所在的供区"
            )
        print(target_area)
        # 提取目标供区的数据，组装成OptimizationInput格式
        area_data = area_stats[target_area]
        
        # 尝试调用接口获取量测
        meas_data = defaultdict(dict)
        try:
            api_url = os.getenv("MEAS_URL")
            payload = meas_request_data(area_data)
            headers = {
                "Content-Type": "application/json"
            }
            print(payload)
            print()
            current_time = datetime.now() - timedelta(minutes=5)
            adj_min = (current_time.minute // 5) * 5
            current_min = datetime.now().replace(minute=adj_min, second=0, microsecond=0)
            dataTime = current_min.strftime("%Y-%m-%d %H:%M:%S")
            params = {
                "dataTime": dataTime
            }
            response = requests.post(api_url, json=payload, headers=headers, timeout=10)
            # 检查响应状态码是否为2xx
            if response.status_code // 100 == 2:
                # API调用成功，处理返回的数据
                meas_data = response.json()
            else:
                # API调用失败，使用默认数据
                print(f"API调用失败（状态码: {response.status_code}）")
        except requests.exceptions.RequestException as e:
            # 网络错误或超时，使用默认数据
            print(f"API调用异常（{str(e)}）")
        except Exception as e:
            print(f"获取量测数据时发生错误: {str(e)}")
        print(meas_data)
        # 主变量测赋值
        for tr in meas_data['tr']:
            target_id = tr['transfm_id']
            new_p_current = tr['val'] if tr['val'] is not None else 0
            if "主变" in area_data and isinstance(area_data["主变"], list):
                for item in area_data["主变"]:
                    if "id" in item and str(item["id"]) == target_id:
                        if "p_current" in item:
                            item["p_current"] = new_p_current
            # zones量测赋值
            if "zones" in area_data and isinstance(area_data["zones"], dict):
                for zone_name, zone_data in area_data["zones"].items():
                    if "capacity" in zone_data and isinstance(zone_data["capacity"], list):
                        for item in zone_data["capacity"]:
                            if "id" in item and str(item["id"]) == target_id:
                                if "p_current" in item:
                                    item["p_current"] = new_p_current
    
        # operating_units、backup_units、hydro_units量测赋值
        for unit in meas_data['units']:
            target_id = unit['id']
            new_p_current = unit['val'] if unit['val'] is not None else 0
            if "operating_units" in area_data and isinstance(area_data["operating_units"], dict):
                for unit_id, unit_data in area_data["operating_units"].items():
                    if "id" in unit_data and str(unit_data["id"]) == target_id:
                        if "p_current" in unit_data:
                            unit_data["p_current"] = new_p_current if new_p_current is not None else 0
        
            if "backup_units" in area_data and isinstance(area_data["backup_units"], dict):
                for unit_id, unit_data in area_data["backup_units"].items():
                    if "id" in unit_data and str(unit_data["id"]) == target_id:
                        if "p_current" in unit_data:
                            unit_data["p_current"] = new_p_current if new_p_current is not None else 0
       
            if "hydro_units" in area_data and isinstance(area_data["hydro_units"], dict):
                for unit_id, unit_data in area_data["hydro_units"].items():
                    if "id" in unit_data and str(unit_data["id"]) == target_id:
                        if "p_current" in unit_data:
                            unit_data["p_current"] = new_p_current if new_p_current is not None else 0
        
        # storage_units量测赋值
        for plant in meas_data['plant']:
            target_id = plant['id']
            new_p_current = plant['val'] if plant['val'] is not None else 0
            if "storage_units" in area_data and isinstance(area_data["storage_units"], dict):
                for unit_name, unit_data in area_data["storage_units"].items():
                    if "st_id" in unit_data and unit_data["st_id"] == target_id:
                        if "p_current" in unit_data:
                            unit_data["p_current"] = new_p_current if new_p_current is not None else 0

        # 设置zones的fix_load
        for zone_name, zone_data in area_data["zones"].items():
            total_p_current = 0.0
            # 计算capacity中所有p_current的和
            if "capacity" in zone_data and isinstance(zone_data["capacity"], list):
                for item in zone_data["capacity"]:
                    if "p_current" in item and item["p_current"] is not None:
                        total_p_current += item["p_current"]
            # 将结果赋值给fixed_load
            zone_data["fixed_load"] = [total_p_current] * 4

        # 构建OptimizationInput对象
        optimization_input = {
            "horizon": 4,  # 默认时间步长为4
            "zones": {},
            "substations": {},
            "objective": "minimize_switch_operation",
            "load_enlarge_factor": load_enlarge_factor,
            "operating_units": area_data.get("operating_units", {}),
            "backup_units": area_data.get("backup_units", {}),
            "hydro_units": area_data.get("hydro_units", {}),
            "storage_units": area_data.get("storage_units", {}),
            "interruptible_loads": {}
        }
        # 添加zones数据
        for zone_name, zone_data in area_data.get("zones", {}).items():
            zone_capacity = sum(transformer.get("capacity", 0) for transformer in zone_data.get("capacity", []))
            zone_var_load = [sum(area_data["联络变电站"][st]["transformers"][tr]["load"][t] for st in area_data["联络变电站"] for tr in area_data["联络变电站"][st]["transformers"]) if zone_name == target_area else 0 for t in range(4)]
            print(zone_name,zone_data["fixed_load"],zone_var_load)
            # 去除device_name对应的主变、以及剩下最大一台主变的容量
            if zone_name == target_area:
                for transformer in area_data["主变"]:
                    if transformer["name"] == best_match:
                        zone_capacity -= transformer.get("capacity", 0)
                # 找出剩余主变中容量最大的一台并去除
                remaining_transformers = [transformer for transformer in area_data.get("主变", []) if transformer.get("name", "") != best_match]
                if remaining_transformers:
                    zone_capacity -= max(transformer.get("capacity", 0) for transformer in remaining_transformers)
            optimization_input["zones"][zone_name] = {
                "capacity": zone_capacity,
                "fixed_load": [zone_data["fixed_load"][t] - zone_var_load[t] for t in range(4)]
            }
            # if zone_name == target_area:
            #     optimization_input["zones"][zone_name] = {
            #         "capacity": zone_capacity,
            #         "fixed_load": [1.8*zone_capacity - zone_var_load[t] for t in range(4)]
            #     }
            # else:
            #     optimization_input["zones"][zone_name] = {
            #         "capacity": zone_capacity,
            #         "fixed_load": [0.5 * zone_capacity] * 4
            #     }
        
        # 添加substations数据
        for substation_name, substation_data in area_data.get("联络变电站", {}).items():
            # 构建变电站数据
            substation = {
                "name": substation_name,
                "nodes": substation_data.get("nodes"),
                "transformers": {},
                "switches": {},
                "zone_lines": substation_data.get("zone_lines"),
                "operation_cost": 3.0,
                "available": True,
                "st_id": substation_data.get("st_id")
            }
            
            # 添加变压器数据
            for transformer_name, transformer_data in substation_data.get("transformers", {}).items():
                substation["transformers"][transformer_name] = {
                    "conn_node": transformer_data.get("conn_node"),
                    "load": transformer_data.get("load"),
                    "sensitivity": transformer_data.get("sensitivity", dict([(zone_name,1) for zone_name in optimization_input["zones"].keys()])),
                    "cost": transformer_data.get("cost", dict([(zone_name,100) for zone_name in optimization_input["zones"].keys()])),
                    "allocate": transformer_data.get("allocate",None)
                }
                
            # 添加开关数据
            for switch_name, switch_data in substation_data.get("switches", {}).items():
                if any(keyword in switch_name for keyword in ["中性点","隔直"]):
                    continue
                substation["switches"][switch_name] = switch_data
            
            # 添加变电站到substations字典
            optimization_input["substations"][substation_name] = substation
        # print(optimization_input)
        # 返回OptimizationInput对象
        return optimization_input
        
    except Exception as e:
        import traceback
        print("发生异常：")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

# 生成量测数据请求payload
def meas_request_data(data):
    result = {
        "tr": [str(zone["id"]) for zone in sum([item["capacity"] for item in data["zones"].values()], []) if "id" in zone],
        "units": [str(item["id"]) for item in data["operating_units"].values()] + [str(item["id"]) for item in data["backup_units"].values()] + [str(item["id"]) for item in data["hydro_units"].values()],
        "plant": [str(unit["st_id"]) for unit in data["storage_units"].values()]
    }
    return result

def get_power_grid_data():
    """
    获取电网全量数据，用于后续分析
    """
    try:
        # 尝试调用API接口获取电网数据
        api_url = os.getenv("POWER_FLOW_URL")
        current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
        dataTime = current_hour.strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "dataTime": dataTime
        }
        response = requests.post(api_url, params=params, timeout=180)
        # 检查响应状态码是否为2xx
        if response.status_code // 100 == 2:
            # API调用成功，处理返回的数据
            pf_data = response.json()
            net = create_network(pf_data)
            # net = pp.from_sqlite("./net.db")
            print("network loaded")
            # 分析网络，找出移除高压母线后的孤岛，并统计每个孤岛中包含"xx变"的母线
            island_stats, bus_to_island, islands, substation_to_island = (
                find_islands_without_high_voltage_buses(net)
            )
            # 识别联络变电站并收集相关设备信息
            interconnection_substation_info = identify_interconnection_substations(
                net, island_stats, bus_to_island, islands, substation_to_island
            )
            #去除各个供区的island_buses
            for k in island_stats.keys():
                del island_stats[k]["island_buses"]
            # 保存结果到JSON文件
            with open("result/area_statistics.json", "w", encoding="utf-8") as f:
                json.dump(island_stats, f, ensure_ascii=False, indent=4)
                print("结果已保存到result/area_statistics.json文件",dataTime)
            # 修改文件格式
            return f"成功从API获取到电网数据\n结果已保存到result/area_statistics.json文件"
        else:
            # API调用失败，使用默认数据
            return f"API调用失败（状态码: {response.status_code}）"
    except requests.exceptions.RequestException as e:
        # 网络错误或超时，使用默认数据
        return f"API调用异常（{str(e)}）"
    except Exception as e:
        return f"获取电网数据时发生错误: {str(e)}"

import threading
import time

def periodic_task(interval, func, *args, **kwargs):
    """周期执行函数
    :param interval: 间隔时间(秒)
    :param func: 要执行的函数
    """
    def wrapper():
        while not event.is_set():
            func(*args, **kwargs)
            time.sleep(interval)
    
    event = threading.Event()
    thread = threading.Thread(target=wrapper)
    thread.daemon = True  # 主线程退出时自动停止
    thread.start()
    return event  # 返回控制句柄

if __name__ == "__main__":
    import uvicorn
    stop_event = periodic_task(3600, get_power_grid_data)  # 每30分钟执行一次，获取全量潮流数据
    uvicorn.run(app, host="0.0.0.0", port=8001)