#!/usr/bin/env python3
"""
Redis工具脚本，用于管理system_prompt

使用方法:
1. 设置system_prompt: python redis_utils.py set "your_prompt_content"
2. 获取system_prompt: python redis_utils.py get
3. 删除system_prompt: python redis_utils.py delete
"""

import os
import sys
import redis
from dotenv import load_dotenv

load_dotenv()

def get_redis_client():
    """获取Redis客户端"""
    redis_host = os.getenv('REDIS_HOST', 'localhost')
    redis_port = int(os.getenv('REDIS_PORT', 6379))
    redis_db = int(os.getenv('REDIS_DB', 0))
    redis_password = os.getenv('REDIS_PASSWORD', None)
    
    return redis.Redis(
        host=redis_host,
        port=redis_port,
        db=redis_db,
        password=redis_password,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5
    )

def set_system_prompt(prompt_content):
    """设置system_prompt到Redis"""
    try:
        r = get_redis_client()
        prompt_key = os.getenv('REDIS_SYSTEM_PROMPT_KEY', 'system_prompt')
        r.set(prompt_key, prompt_content)
        print(f"Successfully set system prompt to Redis key: {prompt_key}")
        return True
    except Exception as e:
        print(f"Failed to set system prompt to Redis: {e}")
        return False

def get_system_prompt():
    """从Redis获取system_prompt"""
    try:
        r = get_redis_client()
        prompt_key = os.getenv('REDIS_SYSTEM_PROMPT_KEY', 'system_prompt')
        prompt = r.get(prompt_key)
        if prompt:
            print(f"System prompt from Redis key '{prompt_key}':")
            print("-" * 50)
            print(prompt)
            print("-" * 50)
        else:
            print(f"No system prompt found in Redis key: {prompt_key}")
        return prompt
    except Exception as e:
        print(f"Failed to get system prompt from Redis: {e}")
        return None

def delete_system_prompt():
    """从Redis删除system_prompt"""
    try:
        r = get_redis_client()
        prompt_key = os.getenv('REDIS_SYSTEM_PROMPT_KEY', 'system_prompt')
        result = r.delete(prompt_key)
        if result:
            print(f"Successfully deleted system prompt from Redis key: {prompt_key}")
        else:
            print(f"No system prompt found to delete in Redis key: {prompt_key}")
        return result
    except Exception as e:
        print(f"Failed to delete system prompt from Redis: {e}")
        return False

def test_redis_connection():
    """测试Redis连接"""
    try:
        r = get_redis_client()
        r.ping()
        print("Redis connection successful!")
        return True
    except Exception as e:
        print(f"Redis connection failed: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python redis_utils.py test                    # 测试Redis连接")
        print("  python redis_utils.py set 'prompt_content'    # 设置system_prompt")
        print("  python redis_utils.py get                     # 获取system_prompt")
        print("  python redis_utils.py delete                  # 删除system_prompt")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "test":
        test_redis_connection()
    elif command == "set":
        if len(sys.argv) < 3:
            print("Error: Please provide prompt content")
            print("Usage: python redis_utils.py set 'your_prompt_content'")
            sys.exit(1)
        prompt_content = sys.argv[2]
        set_system_prompt(prompt_content)
    elif command == "get":
        get_system_prompt()
    elif command == "delete":
        delete_system_prompt()
    else:
        print(f"Unknown command: {command}")
        print("Available commands: test, set, get, delete")
        sys.exit(1)