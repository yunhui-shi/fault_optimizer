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
app.mount("/static", StaticFiles(directory="static"), name="static")
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

@app.get("/get_optimization_boundary", tags=["Optimization"])
def get_optimization_boundary(device_name: str, device_type: Literal["线路", "母线", "主变"]):
    """
    根据设备名称和类型获取对应供区的优化边界数据
    
    - **device_name**: 设备名称，例如"双龙变1号主变"
    - **device_type**: 设备类型，可选值为"线路"、"母线"、"主变"
    - **返回**: 包含该设备所在供区数据的OptimizationInput对象
    """
    try:
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
        
        # 如果没有找到对应的供区，返回错误
        if not target_area:
            raise HTTPException(
                status_code=404,
                detail=f"未找到设备 {device_name} ({device_type}) 所在的供区"
            )
        
        # 提取目标供区的数据，组装成OptimizationInput格式
        area_data = area_stats[target_area]
        
        # 构建OptimizationInput对象
        optimization_input = {
            "horizon": 4,  # 默认时间步长为4
            "zones": {},
            "substations": {},
            "objective": "MIN_SWITCH_OP",
            "operating_units": area_data.get("operating_units", {}),
            "backup_units": area_data.get("backup_units", {}),
            "hydro_units": area_data.get("hydro_units", {}),
            "storage_units": area_data.get("storage_units", {}),
            "interruptible_loads": {}
        }
        
        # 添加zones数据
        for zone_name, zone_data in area_data.get("zones", {}).items():
            zone_capacity = sum(transformer.get("capacity", 0) for transformer in zone_data.get("capacity", []))
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
                "fixed_load": zone_data.get("fixed_load", [])
            }
        
        # 添加substations数据
        for substation_name, substation_data in area_data.get("联络变电站", {}).items():
            # 构建变电站数据
            substation = {
                "transformers": {},
                "switches": {},
                "zone_lines": {},
                "operation_cost": 1000.0,
                "available": True
            }
            
            # 添加变压器数据
            for transformer_name, transformer_data in substation_data.get("transformers", {}).items():
                substation["transformers"][transformer_name] = {
                    "conn_node": transformer_data.get("conn_node"),
                    "load": transformer_data.get("load"),
                    "sensitivity": transformer_data.get("sensitivity", {}),
                    "cost": transformer_data.get("cost", {})
                }
                
            # 添加开关数据
            for switch_name, switch_data in substation_data.get("switches", {}).items():
                substation["switches"][switch_name] = {
                    "available": switch_data.get("available", True),
                    "cost": switch_data.get("cost", 1),
                    "initial_state": switch_data.get("initial_state", 0),
                    "nodes": switch_data.get("nodes", []),
                    "switch_type": switch_data.get("switch_type", "breaker")
                }
                
            # 添加线路数据
            for line_name, line_data in substation_data.get("zone_lines", {}).items():
                substation["zone_lines"][line_name] = {
                    "available": line_data.get("available", True),
                    "conn_node": line_data.get("conn_node", ""),
                    "zone": line_data.get("zone", "")
                }
            
            # 添加变电站到substations字典
            optimization_input["substations"][substation_name] = substation
        
        # 返回OptimizationInput对象
        return optimization_input
        
    except Exception as e:
        import traceback
        print("发生异常：")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)