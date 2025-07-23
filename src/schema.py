# --- Pydantic 模型定义 ---
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Tuple, Literal
from enum import Enum
import json
class Zone(BaseModel):
    """区域定义，负荷变为时间序列"""
    capacity: float = Field(..., description="供区最大供电能力 (MW)")
    fixed_load: List[float] = Field(..., description="区域内的固定负荷时间序列 (MW)")

class Transformer(BaseModel):
    """主变定义，负荷变为时间序列，并增加供电成本"""
    load: List[float] = Field(..., description="主变承载的负荷时间序列 (MW)")
    conn_node: str = Field(..., description="主变在拓扑中的连接点名称")
    sensitivity: Dict[str, float] = Field(..., description="主变在不同区域的灵敏度")
    cost: Dict[str, float] = Field(..., description="主变在不同区域的成本（例如 $/MW）")
    allocate: Optional[str] = Field(None, description="主变所属区域")

class ZoneLine(BaseModel):
    """供区线路定义，增加初始投运状态"""
    zone: str = Field(..., description="该线路归属的供区")
    conn_node: str = Field(..., description="线路在拓扑中的连接点名称")
    available: bool = Field(True, description="线路是否可用，True表示可用，False表示不可用")

class Switch(BaseModel):
    """开关定义，包含连接节点、初始状态和操作成本"""
    nodes: Tuple[str, str] = Field(..., description="开关连接的两个节点")
    initial_state: int = Field(..., description="开关的初始状态，1表示闭合，0表示断开")
    cost: float = Field(1.0, description="开关单次操作的成本")
    available: bool = Field(True, description="开关是否可用，True表示可用，False表示不可用")
    switch_type: Literal["breaker", "switch"] = Field("switch", description="开关类型，breaker或switch")
    
class ObjectiveType(Enum):
    MIN_SWITCH_OP = "minimize_switch_operation"
    MAX_SAFETY_REGION = "maximize_safety_region"
    MIN_COST = "minimize_gen_cost"
    
class Objective(BaseModel):
    type: ObjectiveType = Field(..., description="目标函数类型，3选1：最小化开关操作、最大化安全裕度、最小化发电成本")

class OperatingUnit(BaseModel):
    zone: str = Field(..., description="机组所属区域")
    p_min: float = Field(..., description="最小技术出力 (MW)")
    p_max: float = Field(..., description="最大技术出力 (MW)")
    cost: float = Field(..., description="发电成本 ($/MWh)")
    sensitivity: float = Field(..., description="敏感度")
    p_current: float = Field(0.0, description="当前出力 (MW)")
    
class BackupUnit(BaseModel):
    zone: str = Field(..., description="机组所属区域")
    p_min: float = Field(..., description="最小技术出力 (MW)")
    p_max: float = Field(..., description="最大技术出力 (MW)")
    cost: float = Field(..., description="发电成本 ($/MWh)")
    startup_cost: float = Field(..., description="单次启动成本 ($)")
    sensitivity: float = Field(..., description="敏感度")
    available: bool = Field(True, description="机组是否可用，True表示可用，False表示不可用")

class HydroUnit(BaseModel):
    zone: str = Field(..., description="机组所属区域")
    p_max: float = Field(..., description="最大技术出力 (MW)")
    cost: float = Field(..., description="发电成本 ($/MWh), 通常较低")
    sensitivity: float = Field(..., description="敏感度")
    available: bool = Field(True, description="机组是否可用，True表示可用，False表示不可用")

class StorageUnit(BaseModel):
    zone: str = Field(..., description="储能所属区域")
    p_charge_max: float = Field(..., description="最大充电功率 (MW)")
    p_discharge_max: float = Field(..., description="最大放电功率 (MW)")
    soc_min: float = Field(..., description="最小荷电状态 (MWh)")
    soc_max: float = Field(..., description="最大荷电状态 (MWh)")
    soc_initial: float = Field(..., description="初始荷电状态 (MWh)")
    sensitivity: float = Field(..., description="敏感度")
    p_current: float = Field(0.0, description="当前出力 (MW)，正值为放电，负值为充电")

class InterruptibleLoad(BaseModel):
    zone: str = Field(..., description="可中断负荷所属区域")
    shed_max: float = Field(..., description="最大可切除负荷 (MW)")
    cost: float = Field(..., description="切负荷成本 ($/MWh), 通常非常高")
    sensitivity: float = Field(..., description="敏感度")

class OptimizationInput(BaseModel):
    """定义POST请求体的结构"""
    horizon: int
    zones: Dict[str, Zone]
    substation_nodes: List[str]
    transformers: Dict[str, Transformer]
    zone_lines: Dict[str, ZoneLine]
    switches: Dict[str, Switch]
    objective: ObjectiveType = ObjectiveType.MIN_SWITCH_OP
    operating_units: Optional[Dict[str, OperatingUnit]] = {}
    backup_units: Optional[Dict[str, BackupUnit]] = {}
    hydro_units: Optional[Dict[str, HydroUnit]] = {}
    storage_units: Optional[Dict[str, StorageUnit]] = {}
    interruptible_loads: Optional[Dict[str, InterruptibleLoad]] = {}

    class Config:
        json_schema_extra = {
            "example": json.loads(open("example/export.json","r",encoding="utf-8").read())
        }