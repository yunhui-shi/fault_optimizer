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
import json
import re
from utils import save_tickets_to_excel
load_dotenv(".env.example")
# Get API keys and endpoints from environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE") # Optional
FAULT_OPT_URL = os.getenv("FAULT_OPT_URL")
N1_ANALYSIS_URL = os.getenv("N1_ANALYSIS_URL", "http://127.0.0.1:9999/n1_analysis") # Default if not set
FIND_CONTINGENCY_PLAN_URL = os.getenv("FIND_CONTINGENCY_PLAN_URL","") # Needs to be set if not mocked
FIND_OPERATION_PLAN_URL = os.getenv("FIND_OPERATION_PLAN_URL","")
GET_MODEL_URL  = os.getenv("GET_MODEL_URL","")
GET_BACKUP_LINE_URL = os.getenv("GET_BACKUP_LINE_URL","")
# 初始化数据库
db = OptimizationDatabase()
# db.save_optimization_config(OptimizationInput.Config.json_schema_extra["example"])
def get_optimization_boundary(device_name:str,device_type:Literal["线路", "母线", "主变"],load_enlarge_factor=1) -> str:
    """
    获取优化边界的工具，当识别到新故障时调用
    
    Args:
        device_name: 故障设备的具体名称
        device_type: 故障设备的类型
        load_enlarge_factor: 负荷放大倍数，默认为1
    
    Returns:
        str: 操作结果描述
    """
    try:
        response = requests.get(FAULT_OPT_URL, params={'device_name': device_name,'device_type':device_type,'load_enlarge_factor':load_enlarge_factor}, timeout=30)
        db.save_optimization_config(response.json())
        return f"成功从API获取到设备 {device_name}（{device_type}）的优化边界数据, 请继续执行后续优化"
    except Exception as e:
        return f"获取优化边界时发生错误: {str(e)}"

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
        # 写操作票，保存到 result/temp_ops_ticket.xls
        save_tickets_to_excel({
            "tickets": result['results']['操作票'],
            "description": "故障恢复操作票"
        })
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
    /nothink 当前电网故障设备：{fault_device}
    检索到的事故预案:{plans}
    请在检索到的事故预案中匹配与当前电网故障设备相关或相似的1个或多个预案。

    **指定输出格式：** 必须严格按照下面的 JSON 格式返回结果。

    输出格式:
    {{
        "预案名称":["黄汤2397线跳闸"], # 匹配事故预案名称
        "预案id":["18934"], #匹配事故预案
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
#### **一、 处置要点**
一句话总结，需要进行什么方式调整，加哪些机组出力、需要增开哪些燃气、水电，需要进行哪些变电站的倒排操作，是否需要启用可中断负荷。
#### **二、 核心优化结果**

| 指标 | 结果 |
| :--- | :--- |
| **运行成本** | `[填入总操作成本，例如: 325000.0]` |
| **操作次数** | `[填入总操作次数，例如: 0]` |

-----

#### **三、 开关操作**

  * **操作总结**: `[此处填写开关操作的总体描述。例如：本次调度周期内无开关操作，系统网络结构保持稳定。或：本次调度共执行 N 次操作以优化潮流分布。]`

  * **具体开关操作顺序列表 (如有)**:

      * `1`: （合上/拉开） `[开关/断路器名称]`。
      * `2: （合上/拉开） `[开关/断路器名称]`。
      * *(如果没有操作，请在此处填写 "无")*

-----

#### **四、 机组调度**

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

#### **五、 网络拓扑**

**1. 主变所属供区:**

  * `[变压器名称, 如: T1]`:  分配至 **`[供区名称, 如: Zone_A]`**
  * `[变压器名称, 如: T2]`:  分配至 **`[供区名称, 如: Zone_A]`**

**2. 各供区供电裕度:**

  * 以表格形式列出各供区的供电裕度（<span style="color:green;">**`[安全/告警/越限]`**</span>）、容量（`[容量值] MW`）和各个时段供电裕度（如：**`[裕度百分比]`%** (负载 `[负载值]` MW)），各供区名称（如: Zone_A）放于第一列，总列数根据时段数量增减。


"""
    
    # try:
    #     # 从环境变量获取Redis配置
    #     redis_host = os.getenv('REDIS_HOST', 'localhost')
    #     redis_port = int(os.getenv('REDIS_PORT', 6379))
    #     redis_db = int(os.getenv('REDIS_DB', 0))
    #     redis_password = os.getenv('REDIS_PASSWORD', None)
        
    #     # 连接Redis
    #     r = redis.Redis(
    #         host=redis_host,
    #         port=redis_port,
    #         db=redis_db,
    #         password=redis_password,
    #         decode_responses=True,
    #         socket_timeout=5,
    #         socket_connect_timeout=5
    #     )
        
    #     # 获取system_prompt
    #     prompt_key = os.getenv('REDIS_SYSTEM_PROMPT_KEY', 'fault_agent_prompt')
    #     redis_prompt = r.get(prompt_key)
        
    #     if redis_prompt:
    #         print(f"Successfully loaded system prompt from Redis key: {prompt_key}")
    #         return redis_prompt
    #     else:
    #         print(f"No system prompt found in Redis key: {prompt_key}, using default")
    #         return default_prompt
            
    # except Exception as e:
    #     print(f"Failed to get system prompt from Redis: {e}, using default")
    #     return default_prompt
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