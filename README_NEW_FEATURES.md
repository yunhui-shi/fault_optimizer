# 故障代理优化器 - 新功能说明

## 概述

本项目已成功实现了三个主要的新功能：

1. **SQLite数据库集成** - 用于存储和管理优化配置数据
2. **故障边界获取工具** - 自动检测新故障并获取优化边界
3. **数据库驱动的优化** - 从数据库中获取参数运行优化

## 新功能详细说明

### 1. SQLite数据库集成

#### 文件：`database.py`

- **类**：`OptimizationDatabase`
- **功能**：创建和管理SQLite数据库，存储优化配置数据
- **主要方法**：
  - `save_optimization_config(config_data)` - 保存配置
- `get_optimization_config()` - 获取配置
  - `list_configs()` - 列出所有配置

#### 数据库表结构

数据库包含以下表：
- `optimization_configs` - 主配置表
- `zones` - 区域信息
- `transformers` - 变压器信息
- `switches` - 开关信息
- `zone_lines` - 区域线路
- `operating_units` - 运行机组
- `backup_units` - 备用机组
- `hydro_units` - 水电机组
- `storage_units` - 储能单元
- `interruptible_loads` - 可中断负荷
- `objectives` - 目标函数
- `substation_nodes` - 变电站节点

### 2. 故障边界获取工具

#### 功能：`get_optimization_boundary(device_name, device_type)`

- **触发条件**：用户输入包含 `/newfaultactivated`
- **参数**：
  - `device_name` - 设备名称
  - `device_type` - 设备类型（"线路"、"母线"、"主变"）
- **工作流程**：
  1. 尝试调用外部API获取优化边界
  2. 如果API调用失败（非2xx状态码），则使用默认配置
  3. 将配置数据保存到数据库
  4. 更新当前状态

#### API配置

- **端点**：`http://localhost:8080/api/optimization-boundary`
- **方法**：POST
- **请求体**：
  ```json
  {
    "device_name": "设备名称",
    "device_type": "设备类型"
  }
  ```

### 3. 数据库驱动的优化

#### 修改：`run_optimization()` 函数

- **原来**：从 `status["current_grid_model"]` 获取参数
- **现在**：从数据库中获取当前配置的参数
- **回退机制**：如果数据库中没有配置，使用默认配置并保存

## 数据结构更新

### Switch类重构

#### 文件：`schema.py`

原来的三个独立参数：
```python
switches: Dict[str, Tuple[str, str]]
initial_sw_states: Dict[str, int]
switch_costs: Dict[str, float]
```

现在合并为一个Switch类：
```python
class Switch(BaseModel):
    nodes: Tuple[str, str]
    initial_state: int
    cost: float

switches: Dict[str, Switch]
```

#### 文件：`optimization_solver.py`

相应更新了 `solve_dynamic_recovery_model` 函数以适应新的数据结构。

## 使用示例

### 1. 基本使用

```python
from agent import get_optimization_boundary, run_optimization

# 检测到新故障时
result = get_optimization_boundary("线路A", "线路")
print(result)

# 运行优化
opt_result = run_optimization()
print(opt_result)
```

### 2. 数据库操作

```python
from database import OptimizationDatabase

db = OptimizationDatabase("optimization.db")

# 保存配置
config_id = db.save_optimization_config("my_config", config_data)

# 获取配置
config = db.get_optimization_config("my_config")

# 列出所有配置
configs = db.list_configs()
```

### 3. Agent使用

当用户输入包含 `/newfaultactivated` 时，Agent会：

1. 自动识别故障信息
2. 调用 `get_optimization_boundary` 工具
3. 获取或生成优化配置
4. 运行优化模型

示例输入：
```
/newfaultactivated 线路A故障，设备类型：线路
```

## 测试脚本

项目包含以下测试脚本：

1. **`test_database.py`** - 测试数据库功能
2. **`test_agent.py`** - 测试Agent模块功能
3. **`demo_fault_activation.py`** - 演示故障激活完整流程

运行测试：
```bash
python3 test_database.py
python3 test_agent.py
python3 demo_fault_activation.py
```

## 配置文件

### 数据库文件
- 默认位置：`optimization.db`
- 自动创建表结构
- 支持多配置存储

### API配置
- 默认端点：`http://localhost:8080/api/optimization-boundary`
- 超时时间：10秒
- 自动回退到默认配置

## 错误处理

1. **API调用失败**：自动使用默认配置并保存到数据库
2. **数据库错误**：记录错误日志并尝试重新连接
3. **配置不存在**：使用默认配置并保存
4. **优化求解失败**：返回错误信息和状态

## 性能优化

1. **数据库连接复用**：使用单例模式管理数据库连接
2. **配置缓存**：避免重复查询相同配置
3. **批量操作**：支持批量保存和查询
4. **索引优化**：在关键字段上创建索引

## 未来扩展

1. **多数据库支持**：PostgreSQL、MySQL等
2. **配置版本管理**：支持配置历史和回滚
3. **分布式部署**：支持多节点部署
4. **实时监控**：添加性能监控和告警
5. **API认证**：添加API密钥和权限管理

## 注意事项

1. 确保数据库文件有适当的读写权限
2. API端点需要正确配置和启动
3. 默认配置数据需要与实际系统匹配
4. 定期备份数据库文件
5. 监控数据库大小和性能