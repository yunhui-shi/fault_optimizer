from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from schema import *
import asyncio,json
# 从另一个文件导入求解器函数
from optimization_solver import solve_dynamic_recovery_model
# 导入agent执行器
from agent import agent_executor
import logging,os
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
    docs_url=None
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)