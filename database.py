# database.py
import sqlite3
import json
from typing import Dict, Any
from contextlib import contextmanager
from MSchema.schema_engine import SchemaEngine
from sqlalchemy import create_engine

class OptimizationDatabase:
    def __init__(self, db_path: str = "optimization.db"):
        self.db_path = db_path
        self.init_database()
    def init_database(self):
        """初始化数据库表结构"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 创建主配置表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS optimization_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_name TEXT UNIQUE NOT NULL, -- 配置名称，唯一标识
                    horizon INTEGER NOT NULL, -- 优化时间范围
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 创建时间
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- 更新时间
                ) -- 优化配置主表，存储优化任务的基本信息
            """)
            
            # 创建区域表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS zones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    zone_name TEXT NOT NULL, -- 区域名称
                    capacity REAL NOT NULL, -- 区域容量
                    fixed_load TEXT NOT NULL, -- 固定负荷，JSON数组格式
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, zone_name)
                ) -- 电力系统区域表，存储各区域的基本信息和负荷数据
            """)
            
            # 创建变压器表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transformers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    transformer_name TEXT NOT NULL, -- 变压器名称
                    load_data TEXT NOT NULL, -- 负荷数据，JSON数组格式
                    conn_node TEXT NOT NULL, -- 连接节点
                    sensitivity TEXT NOT NULL, -- 灵敏度系数，JSON对象格式
                    cost TEXT NOT NULL, -- 成本参数，JSON对象格式
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, transformer_name)
                ) -- 变压器设备表，存储变压器的负荷、连接和成本信息
            """)
            
            # 创建开关表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS switches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    switch_name TEXT NOT NULL, -- 开关名称
                    node1 TEXT NOT NULL, -- 连接节点1
                    node2 TEXT NOT NULL, -- 连接节点2
                    initial_state INTEGER NOT NULL, -- 初始状态（0关闭，1开启）
                    cost REAL NOT NULL, -- 操作成本
                    available INTEGER NOT NULL DEFAULT 1, -- 是否可用（0不可用，1可用）
                    switch_type TEXT NOT NULL DEFAULT 'switch', -- 开关类型
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, switch_name)
                ) -- 开关设备表，存储开关的连接关系、状态和操作成本
            """)
            
            # 创建区域线路表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS zone_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    line_name TEXT NOT NULL, -- 线路名称
                    zone TEXT NOT NULL, -- 所属区域
                    conn_node TEXT NOT NULL, -- 连接节点
                    available INTEGER NOT NULL DEFAULT 1, -- 是否可用（0不可用，1可用）
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, line_name)
                ) -- 区域间线路表，存储连接不同区域的输电线路信息
            """)
            
            # 创建运行机组表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operating_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    unit_name TEXT NOT NULL, -- 机组名称
                    zone TEXT NOT NULL, -- 所属区域
                    p_min REAL NOT NULL, -- 最小出力（MW）
                    p_max REAL NOT NULL, -- 最大出力（MW）
                    cost REAL NOT NULL, -- 发电成本（元/MWh）
                    sensitivity REAL NOT NULL, -- 灵敏度系数
                    p_current REAL NOT NULL DEFAULT 0.0, -- 当前出力（MW）
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, unit_name)
                ) -- 运行发电机组表，存储正在运行的发电机组的技术参数和成本信息
            """)
            
            # 创建备用机组表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS backup_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    unit_name TEXT NOT NULL, -- 机组名称
                    zone TEXT NOT NULL, -- 所属区域
                    p_min REAL NOT NULL, -- 最小出力（MW）
                    p_max REAL NOT NULL, -- 最大出力（MW）
                    cost REAL NOT NULL, -- 发电成本（元/MWh）
                    startup_cost REAL NOT NULL, -- 启动成本（元）
                    sensitivity REAL NOT NULL, -- 灵敏度系数
                    available INTEGER NOT NULL DEFAULT 1, -- 是否可用（0不可用，1可用）
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, unit_name)
                ) -- 备用发电机组表，存储可启动的备用发电机组信息
            """)
            
            # 创建水电机组表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hydro_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    unit_name TEXT NOT NULL, -- 水电机组名称
                    zone TEXT NOT NULL, -- 所属区域
                    p_max REAL NOT NULL, -- 最大出力（MW）
                    cost REAL NOT NULL, -- 发电成本（元/MWh）
                    sensitivity REAL NOT NULL, -- 灵敏度系数
                    available INTEGER NOT NULL DEFAULT 1, -- 是否可用（0不可用，1可用）
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, unit_name)
                ) -- 水电机组表，存储水力发电机组的技术参数和可用性
            """)
            
            # 创建储能单元表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS storage_units (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    unit_name TEXT NOT NULL, -- 储能单元名称
                    zone TEXT NOT NULL, -- 所属区域
                    p_charge_max REAL NOT NULL, -- 最大充电功率（MW）
                    p_discharge_max REAL NOT NULL, -- 最大放电功率（MW）
                    soc_min REAL NOT NULL, -- 最小荷电状态（%）
                    soc_max REAL NOT NULL, -- 最大荷电状态（%）
                    soc_initial REAL NOT NULL, -- 初始荷电状态（%）
                    sensitivity REAL NOT NULL, -- 灵敏度系数
                    p_current REAL NOT NULL DEFAULT 0.0, -- 当前功率（MW，正值为放电，负值为充电）
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, unit_name)
                ) -- 储能设备表，存储电池等储能设备的技术参数和状态信息
            """)
            
            # 创建可中断负荷表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS interruptible_loads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    load_name TEXT NOT NULL, -- 可中断负荷名称
                    zone TEXT NOT NULL, -- 所属区域
                    shed_max REAL NOT NULL, -- 最大可切除负荷（MW）
                    cost REAL NOT NULL, -- 切负荷成本（元/MWh）
                    sensitivity REAL NOT NULL, -- 灵敏度系数
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id),
                    UNIQUE(config_id, load_name)
                ) -- 可中断负荷表，存储可以在紧急情况下切除的负荷信息
            """)
            
            # 创建目标函数表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS objectives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    obj_type TEXT NOT NULL, -- 目标函数类型（3选1：minimize_switch_operation、maximize_safety_region、minimize_gen_cost）
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id)
                ) -- 优化目标函数表，存储单一优化目标类型（3选1模式）
            """)
            
            # 创建变电站节点表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS substation_nodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, -- 主键ID
                    config_id INTEGER NOT NULL, -- 配置ID，外键
                    node_name TEXT NOT NULL, -- 变电站节点名称
                    FOREIGN KEY (config_id) REFERENCES optimization_configs(id)
                ) -- 变电站节点表，存储电力系统中的变电站节点信息
            """)
            
            conn.commit()
    
    def save_optimization_config(self, config_data: Dict[str, Any]) -> int:
        """保存优化配置数据到数据库"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 清空整个数据库
            tables = ['zones', 'transformers', 'switches', 'zone_lines', 'operating_units',
                     'backup_units', 'hydro_units', 'storage_units', 'interruptible_loads',
                     'objectives', 'substation_nodes', 'optimization_configs']
            
            for table in tables:
                cursor.execute(f"DELETE FROM {table}")
            
            # 插入主配置
            cursor.execute("""
                INSERT INTO optimization_configs (config_name, horizon)
                VALUES (?, ?)
            """, ("default", config_data['horizon']))
            
            config_id = cursor.lastrowid
            
            # 插入区域数据
            for zone_name, zone_data in config_data['zones'].items():
                cursor.execute("""
                    INSERT INTO zones (config_id, zone_name, capacity, fixed_load)
                    VALUES (?, ?, ?, ?)
                """, (config_id, zone_name, zone_data['capacity'], json.dumps(zone_data['fixed_load'])))
            
            # 插入变压器数据
            for transformer_name, transformer_data in config_data['transformers'].items():
                cursor.execute("""
                    INSERT INTO transformers (config_id, transformer_name, load_data, conn_node, sensitivity, cost)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (config_id, transformer_name, json.dumps(transformer_data['load']),
                      transformer_data['conn_node'], json.dumps(transformer_data['sensitivity']),
                      json.dumps(transformer_data['cost'])))
            
            # 插入开关数据
            for switch_name, switch_data in config_data['switches'].items():
                cursor.execute("""
                    INSERT INTO switches (config_id, switch_name, node1, node2, initial_state, cost, available, switch_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (config_id, switch_name, switch_data['nodes'][0], switch_data['nodes'][1],
                      switch_data['initial_state'], switch_data['cost'], 
                      1 if switch_data.get('available', True) else 0,
                      switch_data.get('switch_type', 'switch')))
            
            # 插入区域线路数据
            for line_name, line_data in config_data['zone_lines'].items():
                cursor.execute("""
                    INSERT INTO zone_lines (config_id, line_name, zone, conn_node, available)
                    VALUES (?, ?, ?, ?, ?)
                """, (config_id, line_name, line_data['zone'], line_data['conn_node'],
                      1 if line_data.get('available', True) else 0))
            
            # 插入运行机组数据
            for unit_name, unit_data in config_data.get('operating_units', {}).items():
                cursor.execute("""
                    INSERT INTO operating_units (config_id, unit_name, zone, p_min, p_max, cost, sensitivity, p_current)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (config_id, unit_name, unit_data['zone'], unit_data['p_min'],
                      unit_data['p_max'], unit_data['cost'], unit_data['sensitivity'],
                      unit_data.get('p_current', 0.0)))
            
            # 插入备用机组数据
            for unit_name, unit_data in config_data.get('backup_units', {}).items():
                cursor.execute("""
                    INSERT INTO backup_units (config_id, unit_name, zone, p_min, p_max, cost, startup_cost, sensitivity, available)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (config_id, unit_name, unit_data['zone'], unit_data['p_min'],
                      unit_data['p_max'], unit_data['cost'], unit_data['startup_cost'], unit_data['sensitivity'],
                      1 if unit_data.get('available', True) else 0))
            
            # 插入水电机组数据
            for unit_name, unit_data in config_data.get('hydro_units', {}).items():
                cursor.execute("""
                    INSERT INTO hydro_units (config_id, unit_name, zone, p_max, cost, sensitivity, available)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (config_id, unit_name, unit_data['zone'], unit_data['p_max'],
                      unit_data['cost'], unit_data['sensitivity'],
                      1 if unit_data.get('available', True) else 0))
            
            # 插入储能单元数据
            for unit_name, unit_data in config_data.get('storage_units', {}).items():
                cursor.execute("""
                    INSERT INTO storage_units (config_id, unit_name, zone, p_charge_max, p_discharge_max, 
                                              soc_min, soc_max, soc_initial, sensitivity, p_current)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (config_id, unit_name, unit_data['zone'], unit_data['p_charge_max'],
                      unit_data['p_discharge_max'], unit_data['soc_min'], unit_data['soc_max'],
                      unit_data['soc_initial'], unit_data['sensitivity'], unit_data.get('p_current', 0.0)))
            
            # 插入可中断负荷数据
            for load_name, load_data in config_data.get('interruptible_loads', {}).items():
                cursor.execute("""
                    INSERT INTO interruptible_loads (config_id, load_name, zone, shed_max, cost, sensitivity)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (config_id, load_name, load_data['zone'], load_data['shed_max'],
                      load_data['cost'], load_data['sensitivity']))
            
            # 插入优化目标数据（单一目标）
            objective = config_data.get('objective')
            if objective:
                obj_type = objective
                if hasattr(obj_type, 'value'):
                    obj_type = obj_type.value
                cursor.execute("""
                    INSERT INTO objectives (config_id, obj_type)
                    VALUES (?, ?)
                """, (config_id, obj_type))
            
            # 插入变电站节点数据
            for node_name in config_data['substation_nodes']:
                cursor.execute("""
                    INSERT INTO substation_nodes (config_id, node_name)
                    VALUES (?, ?)
                """, (config_id, node_name))
            
            conn.commit()
            return config_id
    
    def get_optimization_config(self) -> Dict[str, Any]:
        """从数据库获取优化配置数据"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 获取主配置
            cursor.execute("""
                SELECT id, horizon FROM optimization_configs WHERE config_name = ?
            """, ("default",))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            config_id, horizon = result
            config_data = {'horizon': horizon}
            
            # 获取区域数据
            cursor.execute("""
                SELECT zone_name, capacity, fixed_load FROM zones WHERE config_id = ?
            """, (config_id,))
            
            zones = {}
            for zone_name, capacity, fixed_load in cursor.fetchall():
                zones[zone_name] = {
                    'capacity': capacity,
                    'fixed_load': json.loads(fixed_load)
                }
            config_data['zones'] = zones
            
            # 获取变压器数据
            cursor.execute("""
                SELECT transformer_name, load_data, conn_node, sensitivity, cost 
                FROM transformers WHERE config_id = ?
            """, (config_id,))
            
            transformers = {}
            for transformer_name, load_data, conn_node, sensitivity, cost in cursor.fetchall():
                transformers[transformer_name] = {
                    'load': json.loads(load_data),
                    'conn_node': conn_node,
                    'sensitivity': json.loads(sensitivity),
                    'cost': json.loads(cost)
                }
            config_data['transformers'] = transformers
            
            # 获取开关数据
            cursor.execute("""
                SELECT switch_name, node1, node2, initial_state, cost, available, switch_type 
                FROM switches WHERE config_id = ?
            """, (config_id,))
            
            switches = {}
            for switch_name, node1, node2, initial_state, cost, available, switch_type in cursor.fetchall():
                switches[switch_name] = {
                    'nodes': (node1, node2),
                    'initial_state': initial_state,
                    'cost': cost,
                    'available': bool(available),
                    'switch_type': switch_type or 'switch'
                }
            config_data['switches'] = switches
            
            # 获取区域线路数据
            cursor.execute("""
                SELECT line_name, zone, conn_node, available FROM zone_lines WHERE config_id = ?
            """, (config_id,))
            
            zone_lines = {}
            for line_name, zone, conn_node, available in cursor.fetchall():
                zone_lines[line_name] = {
                    'zone': zone,
                    'conn_node': conn_node,
                    'available': bool(available)
                }
            config_data['zone_lines'] = zone_lines
            
            # 获取变电站节点数据
            cursor.execute("""
                SELECT node_name FROM substation_nodes WHERE config_id = ?
            """, (config_id,))
            
            substation_nodes = [row[0] for row in cursor.fetchall()]
            config_data['substation_nodes'] = substation_nodes
            
            # 获取运行机组数据
            cursor.execute("""
                SELECT unit_name, zone, p_min, p_max, cost, sensitivity, p_current 
                FROM operating_units WHERE config_id = ?
            """, (config_id,))
            
            operating_units = {}
            for unit_name, zone, p_min, p_max, cost, sensitivity, p_current in cursor.fetchall():
                operating_units[unit_name] = {
                    'zone': zone, 'p_min': p_min, 'p_max': p_max,
                    'cost': cost, 'sensitivity': sensitivity, 'p_current': p_current
                }
            config_data['operating_units'] = operating_units
            
            # 获取备用机组数据
            cursor.execute("""
                SELECT unit_name, zone, p_min, p_max, cost, startup_cost, sensitivity, available 
                FROM backup_units WHERE config_id = ?
            """, (config_id,))
            
            backup_units = {}
            for unit_name, zone, p_min, p_max, cost, startup_cost, sensitivity, available in cursor.fetchall():
                backup_units[unit_name] = {
                    'zone': zone, 'p_min': p_min, 'p_max': p_max,
                    'cost': cost, 'startup_cost': startup_cost, 'sensitivity': sensitivity,
                    'available': bool(available)
                }
            config_data['backup_units'] = backup_units
            
            # 获取水电机组数据
            cursor.execute("""
                SELECT unit_name, zone, p_max, cost, sensitivity, available 
                FROM hydro_units WHERE config_id = ?
            """, (config_id,))
            
            hydro_units = {}
            for unit_name, zone, p_max, cost, sensitivity, available in cursor.fetchall():
                hydro_units[unit_name] = {
                    'zone': zone, 'p_max': p_max, 'cost': cost, 'sensitivity': sensitivity,
                    'available': bool(available)
                }
            config_data['hydro_units'] = hydro_units
            
            # 获取储能单元数据
            cursor.execute("""
                SELECT unit_name, zone, p_charge_max, p_discharge_max, soc_min, soc_max, soc_initial, sensitivity, p_current 
                FROM storage_units WHERE config_id = ?
            """, (config_id,))
            
            storage_units = {}
            for unit_name, zone, p_charge_max, p_discharge_max, soc_min, soc_max, soc_initial, sensitivity, p_current in cursor.fetchall():
                storage_units[unit_name] = {
                    'zone': zone, 'p_charge_max': p_charge_max, 'p_discharge_max': p_discharge_max,
                    'soc_min': soc_min, 'soc_max': soc_max, 'soc_initial': soc_initial, 'sensitivity': sensitivity, 'p_current': p_current
                }
            config_data['storage_units'] = storage_units
            
            # 获取可中断负荷数据
            cursor.execute("""
                SELECT load_name, zone, shed_max, cost, sensitivity 
                FROM interruptible_loads WHERE config_id = ?
            """, (config_id,))
            
            interruptible_loads = {}
            for load_name, zone, shed_max, cost, sensitivity in cursor.fetchall():
                interruptible_loads[load_name] = {
                    'zone': zone, 'shed_max': shed_max, 'cost': cost, 'sensitivity': sensitivity
                }
            config_data['interruptible_loads'] = interruptible_loads
            
            # 获取目标函数数据（单一目标）
            cursor.execute("""
                SELECT obj_type FROM objectives WHERE config_id = ?
            """, (config_id,))
            
            result = cursor.fetchone()
            if result:
                config_data['objective'] = result[0]
            else:
                config_data['objective'] = None
            
            return config_data
    
    def create_Mschema(self):
        db_engine = create_engine(f'sqlite:///{self.db_path}')
        schema_engine = SchemaEngine(engine=db_engine)
        mschema = schema_engine.mschema
        mschema_str = mschema.to_mschema()
        # print(mschema_str)
        return mschema_str

    def list_configs(self):
        """列出所有配置"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT config_name, created_at, updated_at FROM optimization_configs
                ORDER BY updated_at DESC
            """)
            return cursor.fetchall()
    
    def execute_sql(self, sql: str, params: tuple = None, fetch: bool = True):
        """执行SQL语句
        
        Args:
            sql (str): 要执行的SQL语句
            params (tuple, optional): SQL参数. Defaults to None.
            fetch (bool, optional): 是否返回查询结果. Defaults to True.
            
        Returns:
            list or int: 如果fetch=True返回查询结果，否则返回受影响的行数
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            if fetch:
                return cursor.fetchall()
            else:
                conn.commit()
                return cursor.rowcount
    
    def execute_many_sql(self, sql: str, params_list: list):
        """批量执行SQL语句
        
        Args:
            sql (str): 要执行的SQL语句
            params_list (list): 参数列表
            
        Returns:
            int: 受影响的总行数
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.executemany(sql, params_list)
            conn.commit()
            return cursor.rowcount
    
    def get_table_info(self, table_name: str = None):
        """获取表结构信息
        
        Args:
            table_name (str, optional): 表名，如果为None则返回所有表. Defaults to None.
            
        Returns:
            dict: 表结构信息
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if table_name:
                # 获取指定表的结构
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = cursor.fetchall()
                return {table_name: columns}
            else:
                # 获取所有表名
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
                
                table_info = {}
                for (table,) in tables:
                    cursor.execute(f"PRAGMA table_info({table})")
                    columns = cursor.fetchall()
                    table_info[table] = columns
                
                return table_info