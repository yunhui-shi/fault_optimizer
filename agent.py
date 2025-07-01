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
load_dotenv(".env.example")
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
        api_url = os.getenv("DATA_URL")
        payload = {
            "device_name": device_name,
            "device_type": device_type
        }
        response = requests.post(api_url, json=payload, timeout=10)
        # 检查响应状态码是否为2xx
        if response.status_code // 100 == 2:
            # API调用成功，处理返回的数据
            db.save_optimization_config(response.json())
            return f"成功从API获取到设备 {device_name}（{device_type}）的优化边界数据, 请继续执行后续优化"
        else:
            # API调用失败，使用默认数据
            return f"API调用失败（状态码: {response.status_code}），使用默认配置数据, 请继续执行后续优化"
            
    except requests.exceptions.RequestException as e:
        # 网络错误或超时，使用默认数据
        return f"API调用异常（{str(e)}），使用默认配置数据, 请继续执行后续优化"
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
- If user input contains '/newfaultactivated', call get_optimization_boundary tool first with device name and device type
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

  * **具体操作列表 (如有)**:

      * `1`: 操作 `[开关/断路器名称]`，状态由 `[原始状态]` 变为 `[新状态]`。
      * `2: 操作 `[开关/断路器名称]`，状态由 `[原始状态]` 变为 `[新状态]`。
      * *(如果没有操作，请在此处填写 "无")*

-----

#### **三、 机组调度**

  * **在运机组**:
      * `[机组名称, 如: Coal_B1]`: `[填写运行状态和出力描述，例如：持续运行，在所有时段稳定出力 200.0 MW。]`
  * **备用机组**:
      * `[机组名称, 如: Gas_A1]`: `[填写运行状态和出力描述，例如：全程保持停机，出力为 0 MW。]`
  * **水电机组**:
      * `[机组名称, 如: Hydro_B1]`: `[填写运行状态和出力描述，例如：根据调度指令在 xx:xx 时段启动，峰值出力 xxx MW。]`
  * **储能系统**:
      * `[机组名称]`: `[填写运行状态和出力描述。]`
  ***需求侧响应**:
      * `[可中断负荷名称, 如: IL_A1]`: `[描述负荷削减情况，例如：所有时段内削减量均为 0 MW，未影响用户。或：在 xx:xx 时段削减负荷 xx MW 以保证电网安全。]`

-----

#### **四、 网络拓扑**

**1. 变压器分配:**

  * `[变压器名称, 如: T1]`:  分配至 **`[供区名称, 如: Zone_A]`**
  * `[变压器名称, 如: T2]`:  分配至 **`[供区名称, 如: Zone_A]`**

**2. 各供区安全状态:**

  * **`[供区名称, 如: Zone_A]`**:

      * **状态**: \<span style="color:green;"\>**`[安全/告警/越限]`**\</span\>
      * **容量**: `[容量值] MW`
      * **安全裕度时序 (Safety Margin Timeline)**:
          * `[时间点1]`: **`[裕度百分比]`%** (负载 `[负载值]` MW)
          * `[时间点2]`: **`[裕度百分比]`%** (负载 `[负载值]` MW)
          * `... (根据时间点数量增减)`

  * **`[供区名称, 如: Zone_B]`**:

      * **状态**: \<span style="color:green;"\>**`[安全/告警/越限]`**\</span\>
      * **容量**: `[容量值] MW`
      * **安全裕度时序 (Safety Margin Timeline)**:
          * `[时间点1]`: **`[裕度百分比]`%** (负载 `[负载值]` MW)
          * `[时间点2]`: **`[裕度百分比]`%** (负载 `[负载值]` MW)
          * `... (根据时间点数量增减)`

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

# 创建代理
agent = create_tool_calling_agent(
    llm=llm,
    tools=tools,
    prompt=prompt
)

# 创建代理执行器
agent_executor = AgentExecutor(agent=agent, tools=tools, memory=memory, verbose=True, handle_parsing_errors=True)
if __name__ == "__main__":
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