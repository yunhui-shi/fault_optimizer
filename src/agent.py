from typing import Literal

from langchain_core.tools import StructuredTool
from langchain.agents import create_tool_calling_agent
from langchain.memory import ConversationBufferWindowMemory
from langchain.agents import AgentExecutor
from langchain_core.prompts import ChatPromptTemplate,MessagesPlaceholder
from langchain_openai import ChatOpenAI
from optimization_solver import solve_dynamic_recovery_model
from schema import *
from database import OptimizationDatabase
import requests
import os
import redis
from dotenv import load_dotenv
import logging
import json
import re
import sys
from datetime import datetime, timedelta
import pandapower as pp
from fuzzywuzzy import fuzz, process
from power_flow import create_network, run_powerflow
from area_topology import find_islands_without_high_voltage_buses, identify_interconnection_substations
load_dotenv(".env.example")
# Get API keys and endpoints from environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE") # Optional
N1_ANALYSIS_URL = os.getenv("N1_ANALYSIS_URL", "http://127.0.0.1:9999/n1_analysis") # Default if not set
FIND_CONTINGENCY_PLAN_URL = os.getenv("FIND_CONTINGENCY_PLAN_URL") # Needs to be set if not mocked
FIND_OPERATION_PLAN_URL = os.getenv("FIND_OPERATION_PLAN_URL")
GET_MODEL_URL  = os.getenv("GET_MODEL_URL")
GET_BACKUP_LINE_URL = os.getenv("GET_BACKUP_LINE_URL")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in the environment variables.")
# 初始化数据库
db = OptimizationDatabase()
db.save_optimization_config(OptimizationInput.Config.json_schema_extra["example"])
def get_optimization_boundary(device_name:str,device_type:Literal["线路", "母线", "主变"]) -> str:
    """
    获取优化边界的工具，当识别到新故障时调用
    
    Args:
        device_name: 故障设备的具体名称
        device_type: 故障设备的类型
    
    Returns:
        str: 操作结果描述
    """
    try:
        # 尝试调用API接口获取优化边界
        api_url = os.getenv("FAULT_OPT_URL")
        # payload = {
        #     "device_name": device_name,
        #     "device_type": device_type
        # }
        current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
        dataTime = current_hour.strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "dataTime": dataTime
        }
        response = requests.post(api_url, params=params, timeout=10)
        # 检查响应状态码是否为2xx
        if response.status_code // 100 == 2:
            # API调用成功，处理返回的数据
            json_data = response.json()
            device_exist = False
            if device_type == "主变":
                for key in list(json_data["transformers"].keys()):
                    if (device_name in key) or (key in device_name):
                        del json_data["transformers"][key]
                        device_exist = True
                        break
            elif device_type == "线路":
                for key in list(json_data["zone_lines"].keys()):
                    if (device_name in key) or (key in device_name):
                        json_data["zone_lines"][key]["available"] = False
                        device_exist = True
                        break
            elif device_type == "母线":
                for key in list(json_data["substation_nodes"]):
                    if (device_name in key) or (key in device_name):
                        # 删除母线
                        json_data["substation_nodes"].remove(key)
                        device_exist = True
                        # 删除与母线连接的变压器
                        for tf_key in list(json_data["transformers"].keys()):
                            if json_data["transformers"][tf_key]["conn_node"] == key:
                                del json_data["transformers"][tf_key]
                        # 删除与母线连接的开关
                        for sw_key in list(json_data["switches"].keys()):
                            if key in json_data["switches"][sw_key]["nodes"]:
                                del json_data["switches"][sw_key]
                        # 删除与母线连接的线路
                        for line_key in list(json_data["zone_lines"].keys()):
                            if json_data["zone_lines"][line_key]["conn_node"] == key:
                                del json_data["zone_lines"][line_key]
                        break
            if device_exist:
                print(f"存在故障设备 {device_name}（{device_type}）")
            else:
                print(f"不存在故障设备 {device_name}（{device_type}），已忽略")
            # db.save_optimization_config(json_data)
            # return f"成功从API获取到设备 {device_name}（{device_type}）的优化边界数据, 请继续执行后续优化"
            return f"使用默认配置数据, 请继续执行后续优化"
        else:
            # API调用失败，使用默认数据
            return f"API调用失败（状态码: {response.status_code}），使用默认配置数据, 请继续执行后续优化"
            
    except requests.exceptions.RequestException as e:
        # 网络错误或超时，使用默认数据
        return f"API调用异常（{str(e)}），使用默认配置数据, 请继续执行后续优化"
    except Exception as e:
        return f"获取优化边界时发生错误: {str(e)}"

# 从接口获取量测数据 
# def get_optimization_boundary(device_name: str, device_type: Literal["线路", "母线", "主变"]):
#     """
#     根据设备名称和类型获取对应供区的优化边界数据
    
#     - **device_name**: 设备名称，例如"双龙变1号主变"
#     - **device_type**: 设备类型，可选值为"线路"、"母线"、"主变"
#     - **返回**: 包含该设备所在供区数据的OptimizationInput对象
#     """
#     try:
#         # 读取area_statistics.json文件
#         with open("result/area_statistics.json", "r", encoding="utf-8") as f:
#             area_stats = json.load(f)
        
#         # 先收集所有设备及其所属供区
#         all_devices = {}
        
#         if device_type == "主变":
#             # 收集所有主变及其所属供区
#             for area_name, area_data in area_stats.items():
#                 for transformer in area_data.get("主变", []):
#                     all_devices[transformer.get("name", "")] = area_name
#         else:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"设备类型 {device_type} 暂不支持"
#             )
#     # 使用process.extractOne找到最相似的设备，并直接获取其所属供区
#         best_match, score = process.extractOne(
#             device_name, 
#             all_devices.keys(), 
#             scorer=fuzz.partial_ratio
#         )
#         print(best_match)
#         # 直接从匹配结果获取供区
#         target_area = all_devices[best_match] if best_match else None
        
#         # 如果没有找到对应的供区，返回错误
#         if not target_area:
#             raise HTTPException(
#                 status_code=404,
#                 detail=f"未找到设备 {device_name} ({device_type}) 所在的供区"
#             )
        
#         # 提取目标供区的数据，组装成OptimizationInput格式
#         area_data = area_stats[target_area]
        
#         # 尝试调用接口获取量测
#         meas_data = {}
#         try:
#             api_url = os.getenv("MEAS_URL")
#             payload = meas_request_data(area_data)
#             headers = {
#                 "Content-Type": "application/json"
#             }
#             print(payload)
#             print()
#             current_time = datetime.now() - timedelta(minutes=5)
#             adj_min = (current_time.minute // 5) * 5
#             current_min = datetime.now().replace(minute=adj_min, second=0, microsecond=0)
#             dataTime = current_min.strftime("%Y-%m-%d %H:%M:%S")
#             params = {
#                 "dataTime": dataTime
#             }
#             response = requests.post(api_url, params=params, json=payload, headers=headers, timeout=10)
#             # 检查响应状态码是否为2xx
#             if response.status_code // 100 == 2:
#                 # API调用成功，处理返回的数据
#                 meas_data = response.json()
#             else:
#                 # API调用失败，使用默认数据
#                 print(f"API调用失败（状态码: {response.status_code}）")
#         except requests.exceptions.RequestException as e:
#             # 网络错误或超时，使用默认数据
#             print(f"API调用异常（{str(e)}）")
#         except Exception as e:
#             print(f"获取量测数据时发生错误: {str(e)}")
#         print(meas_data)
#         # 主变量测赋值
#         for tr in meas_data['tr']:
#             target_id = tr['transfm_id']
#             new_p_current = tr['val'] if tr['val'] is not None else 0
#             if "主变" in area_data and isinstance(area_data["主变"], list):
#                 for item in area_data["主变"]:
#                     if "id" in item and str(item["id"]) == target_id:
#                         if "p_current" in item:
#                             item["p_current"] = new_p_current
#             # zones量测赋值
#             if "zones" in area_data and isinstance(area_data["zones"], dict):
#                 for zone_name, zone_data in area_data["zones"].items():
#                     if "capacity" in zone_data and isinstance(zone_data["capacity"], list):
#                         for item in zone_data["capacity"]:
#                             if "id" in item and str(item["id"]) == target_id:
#                                 if "p_current" in item:
#                                     item["p_current"] = new_p_current
    
#         # operating_units、backup_units、hydro_units量测赋值
#         for unit in meas_data['units']:
#             target_id = unit['id']
#             new_p_current = unit['val'] if unit['val'] is not None else 0
#             if "operating_units" in area_data and isinstance(area_data["operating_units"], dict):
#                 for unit_id, unit_data in area_data["operating_units"].items():
#                     if "id" in unit_data and str(unit_data["id"]) == target_id:
#                         if "p_current" in unit_data:
#                             unit_data["p_current"] = new_p_current if new_p_current is not None else 0
        
#             if "backup_units" in area_data and isinstance(area_data["backup_units"], dict):
#                 for unit_id, unit_data in area_data["backup_units"].items():
#                     if "id" in unit_data and str(unit_data["id"]) == target_id:
#                         if "p_current" in unit_data:
#                             unit_data["p_current"] = new_p_current if new_p_current is not None else 0
       
#             if "hydro_units" in area_data and isinstance(area_data["hydro_units"], dict):
#                 for unit_id, unit_data in area_data["hydro_units"].items():
#                     if "id" in unit_data and str(unit_data["id"]) == target_id:
#                         if "p_current" in unit_data:
#                             unit_data["p_current"] = new_p_current if new_p_current is not None else 0
        
#         # storage_units量测赋值
#         for plant in meas_data['plant']:
#             target_id = plant['id']
#             new_p_current = plant['val'] if plant['val'] is not None else 0
#             if "storage_units" in area_data and isinstance(area_data["storage_units"], dict):
#                 for unit_name, unit_data in area_data["storage_units"].items():
#                     if "st_id" in unit_data and unit_data["st_id"] == target_id:
#                         if "p_current" in unit_data:
#                             unit_data["p_current"] = new_p_current if new_p_current is not None else 0

#         # 设置zones的fix_load
#         for zone_name, zone_data in area_data["zones"].items():
#             total_p_current = 0.0
#             # 计算capacity中所有p_current的和
#             if "capacity" in zone_data and isinstance(zone_data["capacity"], list):
#                 for item in zone_data["capacity"]:
#                     if "p_current" in item and item["p_current"] is not None:
#                         total_p_current += item["p_current"]
#             # 将结果赋值给fixed_load
#             zone_data["fixed_load"] = [total_p_current] * 4

#         # 构建OptimizationInput对象
#         optimization_input = {
#             "horizon": 4,  # 默认时间步长为4
#             "zones": {},
#             "substations": {},
#             "objective": "MIN_SWITCH_OP",
#             "operating_units": area_data.get("operating_units", {}),
#             "backup_units": area_data.get("backup_units", {}),
#             "hydro_units": area_data.get("hydro_units", {}),
#             "storage_units": area_data.get("storage_units", {}),
#             "interruptible_loads": {}
#         }
        
#         # 添加zones数据
#         for zone_name, zone_data in area_data.get("zones", {}).items():
#             zone_capacity = sum(transformer.get("capacity", 0) for transformer in zone_data.get("capacity", []))
#             # 去除device_name对应的主变、以及剩下最大一台主变的容量
#             if zone_name == target_area:
#                 for transformer in area_data["主变"]:
#                     if transformer["name"] == best_match:
#                         zone_capacity -= transformer.get("capacity", 0)
#                 # 找出剩余主变中容量最大的一台并去除
#                 remaining_transformers = [transformer for transformer in area_data.get("主变", []) if transformer.get("name", "") != best_match]
#                 if remaining_transformers:
#                     zone_capacity -= max(transformer.get("capacity", 0) for transformer in remaining_transformers)
#             optimization_input["zones"][zone_name] = {
#                 "capacity": zone_capacity,
#                 "fixed_load": zone_data.get("fixed_load", [])
#             }
        
#         # 添加substations数据
#         for substation_name, substation_data in area_data.get("联络变电站", {}).items():
#             # 构建变电站数据
#             substation = {
#                 "transformers": {},
#                 "switches": {},
#                 "zone_lines": {},
#                 "operation_cost": 1000.0,
#                 "available": True
#             }
            
#             # 添加变压器数据
#             for transformer_name, transformer_data in substation_data.get("transformers", {}).items():
#                 substation["transformers"][transformer_name] = {
#                     "conn_node": transformer_data.get("conn_node"),
#                     "load": transformer_data.get("load"),
#                     "sensitivity": transformer_data.get("sensitivity", {}),
#                     "cost": transformer_data.get("cost", {})
#                 }
                
#             # 添加开关数据
#             for switch_name, switch_data in substation_data.get("switches", {}).items():
#                 substation["switches"][switch_name] = {
#                     "available": switch_data.get("available", True),
#                     "cost": switch_data.get("cost", 1),
#                     "initial_state": switch_data.get("initial_state", 0),
#                     "nodes": switch_data.get("nodes", []),
#                     "switch_type": switch_data.get("switch_type", "breaker")
#                 }
                
#             # 添加线路数据
#             for line_name, line_data in substation_data.get("zone_lines", {}).items():
#                 substation["zone_lines"][line_name] = {
#                     "available": line_data.get("available", True),
#                     "conn_node": line_data.get("conn_node", ""),
#                     "zone": line_data.get("zone", "")
#                 }
            
#             # 添加变电站到substations字典
#             optimization_input["substations"][substation_name] = substation  
#         # 返回OptimizationInput对象
#         return optimization_input
#         # db.save_optimization_config(optimization_input)
#         # return f"成功从API获取到设备 {device_name}（{device_type}）的优化边界数据, 请继续执行后续优化"
#     except Exception as e:
#         import traceback
#         print("发生异常：")
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")

# # 生成量测数据请求payload
# def meas_request_data(data):
#     result = {
#         "tr": [str(item["id"]) for item in data["主变"]] + [str(zone["id"]) for zone in sum([item["capacity"] for item in data["zones"].values()], []) if "id" in zone],
#         "units": list(data["operating_units"].keys()) + list(data["backup_units"].keys()) + list(data["hydro_units"].keys()),
#         "plant": [unit["st_id"] for unit in data["storage_units"].values()]
#     }
#     return result


def run_optimization(objective: Literal["MIN_SWITCH_OP", "MAX_SAFETY_REGION", "MIN_COST"] = None):
    """
    运行优化模型并返回结果
    
    Args:
        objective: 优化目标类型，可选值：'MIN_SWITCH_OP'（最小化开关操作）、'MAX_SAFETY_REGION'（最大化安全裕度）、'MIN_COST'（最小化发电成本）
                  如果不指定，则使用数据库中的配置
        the default objective is MIN_SWITCH_OP
    """
    try:
        # 从数据库获取最新配置参数
        data = OptimizationInput(**db.get_optimization_config()).model_dump()
        
        # 如果指定了objective参数，则覆盖数据库中的配置
        if objective:
            from schema import ObjectiveType
            # 将字符串转换为ObjectiveType枚举
            if objective == 'MIN_SWITCH_OP':
                data['objective'] = ObjectiveType.MIN_SWITCH_OP
            elif objective == 'MAX_SAFETY_REGION':
                data['objective'] = ObjectiveType.MAX_SAFETY_REGION
            elif objective == 'MIN_COST':
                data['objective'] = ObjectiveType.MIN_COST        
        # 运行优化
        result = solve_dynamic_recovery_model(**data)
        return result
    except Exception as e:
        return f"运行优化时发生错误: {str(e)}"

from openai import OpenAI, max_retries
modify_optimization_config_client = OpenAI(
    base_url=os.getenv("XIYAN_API_URL"),
    api_key=os.getenv("XIYAN_API_KEY"), # ModelScope Token
)

def modify_optimization_config(query:str):
    """
    修改优化配置的工具，用于用户手动调整优化参数
    
    Args:
        query: 用户的要求
    
    Returns:
        str: 确认信息
    """
    prompt = f"""你是一名SQLite专家，现在需要阅读并理解下面的【数据库schema】描述，以及可能用到的【参考信息】，并运用SQLite知识生成sql的UPDATE语句回答【用户问题】。
【用户问题】
{query}

【数据库schema】
{db.create_Mschema()}

【参考信息】
 如果用户问题中的值与example中的值不同，那么选取最相近的example的值。只生成UPDATE语句

【用户问题】
{query}

```sql"""
    response = modify_optimization_config_client.chat.completions.create(
        model=os.getenv("XIYAN_MODEL"),
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    clause = response.choices[0].message.content
    print(clause)
    db.execute_sql(clause)
    return "优化配置已更新, 请重新优化"
    
def getAuxInfo(fault_device: str, device_type: str) -> str:
    """该tool用于根据指定的初始故障，获取故障的相关辅助信息，例如线路基本信息和场站基本信息，附近的备用供电线路"""
    try:
        response = requests.get(GET_MODEL_URL, params={'name': fault_device,'type':device_type}, timeout=30).json()
        response.update(
            {"备用线路清单":
            requests.get(GET_BACKUP_LINE_URL, params={'lineName': fault_device}, timeout=30).json()
            }
            )
        return json.dumps(response)
    except requests.exceptions.RequestException as e:
        print(f"Error calling GET MODEL API: {e}")
        return f"Error GET MODEL for '{fault_device}': {e}"
    except json.JSONDecodeError:
        print(f"Error decoding JSON response from GET MODEL API for : {fault_device}")
        return f"Error processing GET MODEL result for '{fault_device}': Invalid JSON response."

def N1Analysis(fault_device: str, device_type: str) -> str:
    """
    该tool用于根据指定的初始故障，自动完成N-1安全分析并返回结构化的潮流变化和设备失电信息，便于后续业务处理和决策支持。
Response JSON example:
[
    {
        "预想故障": [
            {
                "设备名称": "浦州44K6线",
                "设备类型": "交流线路"
            }
        ],
        "校核结果": {
            "负荷变化清单": [
                {
                    "负荷变化(MW)": "10.48",
                    "设备名称": "严州变电所3号主变-500kV",
                    "限值(MW)": "1000.00",
                    "设备类型": "变压器绕组",
                    "故障前负载(MW)": "51.39",
                    "故障后负载率(%)": "4.09",
                    "故障前负载率(%)": "5.14",
                    "故障后负载(MW)": "40.91"
                },
                {
                    "负荷变化(MW)": "10.48",
                    "设备名称": "严州变电所3号主变-220kV",
                    "限值(MW)": "1000.00",
                    "设备类型": "变压器绕组",
                    "故障前负载(MW)": "-51.39",
                    "故障后负载率(%)": "-4.09",
                    "故障前负载率(%)": "-5.14",
                    "故障后负载(MW)": "-40.91"
                },
                {
                    "负荷变化(MW)": "10.48",
                    "设备名称": "严州变电所2号主变-高",
                    "限值(MW)": "1000.00",
                    "设备类型": "变压器绕组",
                    "故障前负载(MW)": "51.39",
                    "故障后负载率(%)": "4.09",
                    "故障前负载率(%)": "5.14",
                    "故障后负载(MW)": "40.91"
                },
                {
                    "负荷变化(MW)": "10.48",
                    "设备名称": "严州变电所2号主变-中",
                    "限值(MW)": "1000.00",
                    "设备类型": "变压器绕组",
                    "故障前负载(MW)": "-51.39",
                    "故障后负载率(%)": "-4.09",
                    "故障前负载率(%)": "-5.14",
                    "故障后负载(MW)": "-40.91"
                },
                {
                    "负荷变化(MW)": "18.16",
                    "设备名称": "后浦变220kV母联开关",
                    "限值(MW)": "118751.16",
                    "设备类型": "断路器",
                    "故障前负载(MW)": "2.14",
                    "故障后负载率(%)": "-0.01",
                    "故障前负载率(%)": "0.00",
                    "故障后负载(MW)": "-16.02"
                }
            ],
            "失电设备清单": [
                {
                    "失电设备": "浙江杭州电网.后浦变/220kV.#2主变",
                    "所属厂站": "浙江杭州电网.后浦变"
                },
                {
                    "失电设备": "浙江杭州电网.后浦变/220kV.#1主变",
                    "所属厂站": "浙江杭州电网.后浦变"
                }
            ]
        }
    }
]

    Args:
        fault_device (str): The name of the faulted device. (REQUIRED)
        device_type (str): The type of the faulted device. 限定为：线路、母线、主变。(REQUIRED)
    """
    print(f"Attempting N-1 analysis for: {fault_device} using {N1_ANALYSIS_URL}")
    if not N1_ANALYSIS_URL:
        print(f"Using mocked N-1 analysis for '{fault_device}'.")
        return f"Mocked N-1 analysis result for '{fault_device}': Component is critical under N-1 conditions."
    else:
        try:
            response = requests.get(N1_ANALYSIS_URL, params={'name': fault_device,'type':device_type}, timeout=300)
            response.raise_for_status()
            return json.dumps(response.json())
        except requests.exceptions.RequestException as e:
            print(f"Error calling N-1 Analysis API: {e}")
            return f"Error performing N-1 analysis for '{fault_device}': {e}"
        except json.JSONDecodeError:
            print(f"Error decoding JSON response from N-1 Analysis API for : {fault_device}")
            return f"Error processing N-1 analysis result for '{fault_device}': Invalid JSON response."

def FindContingencyPlan(fault_device:str) -> str:
    """**/no think，不要显示思路** 调用这个工具来获取电网故障设备对应的事故预案.

    Args:
        fault_device: str = Field(..., description="设备名称")
    """
    if not FIND_CONTINGENCY_PLAN_URL:
        return "预案未找到，下一步进行获取配置文件，进行优化"
    plans = requests.get(FIND_CONTINGENCY_PLAN_URL).json()["result"]
    plans.extend(requests.get(FIND_OPERATION_PLAN_URL+f"?name={fault_device}").json())
    plans_short = [
        {
            "预案名称":plan["预案名称"],
            "预案id":plan["预案ID"]
            } for plan in plans
        ]
    action = dict()
    for plan in plans:
        action[plan["预案ID"]] = (plan["预案名称"],plan["预案内容"])
    prompt_con = f"""
    当前电网故障设备：{fault_device}
    检索到的事故预案:{plans_short}
    请在检索到的事故预案中匹配，判定是否有当前电网故障设备相关或相似的1个或多个预案。注意区分单线故障和双线故障，如果发生了单线故障却匹配到了双线故障预案，则相关性仅为中等，反之亦然。

    **指定输出格式：** 必须严格按照下面的 JSON 格式返回结果。

    输出格式:
    {{
        "Found":1 , #若找到设为1，找不到设为0
        "预案名称":["黄汤2397线跳闸"], # 匹配事故预案名称
        "预案id":["18934"], #匹配事故预案
        "其他说明":"预案18934与当前故障场景相关性是高/中/低，原因是..."
    }}
    """
    response = plan_llm.invoke(prompt_con)
    print(response.content)
    matches = re.findall(r'"(\d{5})"',response.content)
    if len(matches):
        return_string = f"为您找到{len(matches)}个预案，包括:\n"
        for plan_id in matches:
            return_string += f"预案名称{action[plan_id][0]}，ID为{plan_id}，采取措施：{action[plan_id][1]}\n"
        think_pattern = r"<think>.*?</think>"
        extra = re.sub(think_pattern,"",response.content,flags=re.DOTALL)
        return_string += f"\n其他情况说明:{extra}"
        return return_string
    else:
        return "预案未找到，下一步进行获取配置文件，进行优化"

tools = [
    StructuredTool.from_function(
        func=run_optimization,
        name="run_optimization",
        description="用于运行电网动态恢复优化的工具。参数从数据库中获取，输出为优化结果。"
    ),
    StructuredTool.from_function(
        func=get_optimization_boundary,
        name="get_optimization_boundary",
        description="获取优化边界的工具。当识别到新故障时调用，传入设备名称和设备类型。优先调用API接口，如果失败则将默认数据存入数据库。",
    ),
    StructuredTool.from_function(
        func=modify_optimization_config,
        name="modify_optimization_config",
        description="用于手动修改优化配置的工具。传入用户的要求，输出为确认信息。"
    ),
    # StructuredTool.from_function(
    #     func=getAuxInfo,
    #     name="getAuxInfo",
    #     description="用于根据指定的初始故障，获取故障的相关辅助信息，例如线路基本信息和场站基本信息，附近的备用供电线路。"
    # ), 
    StructuredTool.from_function(
        func=N1Analysis,
        name="N1Analysis",
        description="用于根据指定的初始故障，自动完成N-1安全分析并返回结构化的潮流变化和设备失电信息，便于后续业务处理和决策支持。"
    ), 
    StructuredTool.from_function(
        func=FindContingencyPlan,
        name="FindContingencyPlan",
        description="用于获取电网故障设备对应的事故预案。"
    ), 
]
memory = ConversationBufferWindowMemory(
        k=5, # Set context window to 5
        memory_key="chat_history", # Key for chat history in prompt
        return_messages=True,    # Return messages instead of strings
        output_key="output"      # Key for agent's final output in memory
    )

def get_system_prompt_from_redis():
    """
    从Redis获取system_prompt，获取失败时返回默认值
    """
    default_prompt = """
You are a helpful assistant that can use tools to help with power grid fault recovery optimization.

Guidelines:
- 如果用户输入包含 '/newfaultactivated', 首先调用FindContingencyPlan工具获取预案， 然后使用get_optimization_boundary工具。
- If user input does not contain '/newfaultactivated', DO NOT call get_optimization_boundary tool. 
- Device types must be one of: "线路", "母线", "主变"
- After getting optimization boundary, you can run the run_optimization tool
- if user input indicates modification of optimization config, for example "某某机组无法启动"、"某某线路/通道不可用"、"某某开关存在缺陷"、"某某发电机出力受限，最高xxMW"、"某某负载负载值xx%", call modify_optimization_config tool.
- when using modify_optimization_config tool, only keep the essential part of the user input, neglect the irrelevant parts.
- if modify_optimization_config tool or get_optimization_boundary tool is called, you must run the run_optimization tool.
- Report optimization results with clear explanations based on the optimization results. It should contain a summary of the optimization results, and the detailed explanations of each time slot.
- DO NOT MODIFY the device name which the user mentioned.
- use the default objective MIN_SWITCH_OP in normal case.

OPTIMIZATION REPORT TEMPLATE 
-----
#### **一、 核心优化结果**

| 指标 | 结果 |
| :--- | :--- |
| **目标函数** | `[填入总目标成本，例如: 32.4998]` |
| **运行成本** | `[填入总操作成本，例如: 325000.0]` |
| **操作次数** | `[填入总操作次数，例如: 0]` |

-----

#### **二、 开关操作**

  * **操作总结**: `[此处填写开关操作的总体描述。例如：本次调度周期内无开关操作，系统网络结构保持稳定。或：本次调度共执行 N 次操作以优化潮流分布。]`

  * **具体开关操作顺序列表 (如有)**:

      * `1`: （合上/拉开） `[开关/断路器名称]`。
      * `2: （合上/拉开） `[开关/断路器名称]`。
      * *(如果没有操作，请在此处填写 "无")*

-----

#### **三、 机组调度**

  * **在运机组**:
      * 以表格形式列出各机组在各个时段出力（如200.0 MW）描述，各机组名称放于第一列，各个时段列于第一行。
  * **备用机组**:
      * 以表格形式列出各备用机组在各个时段出力（如200.0 MW）描述，各机组名称放于第一列，各个时段列于第一行。
  * **水电机组**:
      * 以表格形式列出各水电机组在各个时段出力（如200.0 MW）描述，各机组名称放于第一列，各个时段列于第一行。
  * **储能系统**:
      * 以表格形式列出各储能在各个时段出力（如200.0 MW）描述，各储能名称放于第一列，各个时段列于第一行。
  ***需求侧响应**:
      * 以表格形式列出各可中断负荷在各个时段负荷削减量（如10.0 MW）描述，各可中断负荷名称放于第一列，各个时段列于第一行。

-----

#### **四、 网络拓扑**

**1. 变压器分配:**

  * `[变压器名称, 如: T1]`:  分配至 **`[供区名称, 如: Zone_A]`**
  * `[变压器名称, 如: T2]`:  分配至 **`[供区名称, 如: Zone_A]`**

**2. 各供区安全状态:**

  * 以表格形式列出各供区的状态（\<span style="color:green;"\>**`[安全/告警/越限]`**\</span\>）、容量（`[容量值] MW`）和各个时段安全裕度（如：**`[裕度百分比]`%** (负载 `[负载值]` MW)），各供区名称（如: Zone_A）放于第一列，总列数根据时段数量增减。


"""
    
    try:
        # 从环境变量获取Redis配置
        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_port = int(os.getenv('REDIS_PORT', 6379))
        redis_db = int(os.getenv('REDIS_DB', 0))
        redis_password = os.getenv('REDIS_PASSWORD', None)
        
        # 连接Redis
        r = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )
        
        # 获取system_prompt
        prompt_key = os.getenv('REDIS_SYSTEM_PROMPT_KEY', 'fault_agent_prompt')
        redis_prompt = r.get(prompt_key)
        
        if redis_prompt:
            print(f"Successfully loaded system prompt from Redis key: {prompt_key}")
            return redis_prompt
        else:
            print(f"No system prompt found in Redis key: {prompt_key}, using default")
            return default_prompt
            
    except Exception as e:
        print(f"Failed to get system prompt from Redis: {e}, using default")
        return default_prompt

# 获取system_prompt
system_prompt = get_system_prompt_from_redis()
prompt = ChatPromptTemplate.from_messages([
    ("system",system_prompt),
    MessagesPlaceholder(variable_name="chat_history"),
    ("user", "{input}"),
    MessagesPlaceholder(variable_name="agent_scratchpad")
    ])
# 初始化LLM
llm = ChatOpenAI(model=os.getenv("MAIN_MODEL"), temperature=0) # 建议使用gpt-4o或gpt-4-turbo，它们在工具调用方面表现更佳
plan_llm = ChatOpenAI(model=os.getenv("MAIN_MODEL"), temperature=0) # 建议使用gpt-4o或gpt-4-turbo，它们在工具调用方面表现更佳

# 创建代理
agent = create_tool_calling_agent(
    llm=llm,
    tools=tools,
    prompt=prompt
)

# 创建代理执行器
agent_executor = AgentExecutor(agent=agent, tools=tools, memory=memory, verbose=True, handle_parsing_errors=True)
if __name__ == "__main__":
    # Check for API key before proceeding
    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY is not set. Please create a .env file with your API key.")
        print("Example .env content:")
        print("OPENAI_API_KEY='your_openai_api_key_here'")
        exit(1)

    print(f"N-1 Analysis tool will use: {N1_ANALYSIS_URL}")
    print(f"Find Contingency Plan tool will use: {FIND_CONTINGENCY_PLAN_URL if FIND_CONTINGENCY_PLAN_URL else 'Mocked (URL not set)'}")
    print("Starting FastAPI server...")
    while True:
        # 例如，瓶窑变#1主变故障/newfaultactivated，然后设置Breaker_LineA1不可用
        user_input = input("\n请输入: ")
        try:
            # invoke方法是LangChain推荐的运行可调用对象的方式
            response = agent_executor.invoke({"input": "/nothink "+user_input})
            print(f"AI: {response['output']}")
        except Exception as e:
            print(f"AI: 在处理你的请求时发生错误: {e}")
            # 如果你想看到更详细的错误信息，可以打印完整的异常
            # import traceback
            # traceback.print_exc()