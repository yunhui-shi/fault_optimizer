# optimization_solver.py
from pyscipopt import Model, quicksum
from schema import ObjectiveType, OptimizationInput
from datetime import datetime, timedelta
import json
import networkx as nx
from collections import defaultdict

def generate_tickets(switch_operations: list, switches: dict, source_nodes: set, load_nodes: set, substations: dict = None) -> list:
    """
    生成操作票：k个开关变位的排序，使得每次变位：
    1. 主变与电源存在最短路径
    2. 闸刀变位前后孤岛数量不变化
    
    开关选择的优先级排序为：与上一步操作存在共同节点的闸刀、任意闸刀、任意断路器
    使用贪心算法：每次选择合法操作中优先级最高的，直到排序完成
    
    Args:
        switch_operations: 开关操作列表
        switches: 开关字典
        source_nodes: 电源节点集合
    
    Returns:
        list: 排序后的操作票列表
    """
    if not switch_operations:
        return []
    
    # 构建初始网络图
    G = nx.Graph()
    # 从switch_operations中获取初始状态信息
    initial_states = {}
    for operation in switch_operations:
        initial_states[operation['switch_name']] = operation['initial_state']
    
    for switch_name, switch_data in switches.items():
        u, v = switch_data["nodes"]
        # 根据初始状态添加边
        if switch_data["initial_state"] == 1:
            G.add_edge(u, v, switch=switch_name)
    
    # 待执行的操作
    remaining_operations = switch_operations.copy()
    ordered_tickets = []
    last_operated_nodes = set()
    
    while remaining_operations:
        best_operation = None
        best_priority = -1
        for operation in remaining_operations:
            switch_name = operation['switch_name']
            switch_data = switches[switch_name]
            u, v = switch_data["nodes"]
            switch_type = switch_data["switch_type"]
            # 检查操作的合法性
            if not is_valid_operation(G, u, v, operation, switch_type, source_nodes, load_nodes):
                continue
            
            # 计算优先级
            priority = calculate_priority(u, v, switch_type, last_operated_nodes, operation['action'])
            if priority > best_priority:
                best_priority = priority
                best_operation = operation
        
        if best_operation is None:
            # 如果没有合法操作，选择第一个可用的操作
            raise ValueError("No valid operation found")
        print(best_operation)
        # 执行操作
        switch_name = best_operation['switch_name']
        switch_data = switches[switch_name]
        u, v = switch_data["nodes"]
        
        # 更新网络图
        if best_operation['action'] == 'close':
            G.add_edge(u, v, switch=switch_name)
        else:
            if G.has_edge(u, v):
                G.remove_edge(u, v)
        
        # 创建操作票
        ticket = create_operation_ticket(best_operation, substations)
        ordered_tickets.append(ticket)
        
        # 更新最后操作的节点
        last_operated_nodes = {u, v}
        
        # 从待执行列表中移除
        remaining_operations.remove(best_operation)
    
    return ordered_tickets


def is_valid_operation(G: nx.Graph, u: str, v: str, operation: dict, switch_type: str, source_nodes: set, load_nodes: set) -> bool:
    """
    检查操作是否合法：
    1. 主变与电源存在最短路径
    2. 闸刀变位前后孤岛数量不变化
    """
    # 创建操作后的临时图
    temp_G = G.copy()
    
    if operation['action'] == 'close':
        temp_G.add_edge(u, v)
    else:
        if temp_G.has_edge(u, v):
            temp_G.remove_edge(u, v)
    
    # 检查连通性：确保主要节点与电源节点连通
    # 这里简化处理，假设所有节点都应该与至少一个电源节点连通
    components_before = list(nx.connected_components(G))
    components_after = list(nx.connected_components(temp_G))
    
    # 孤岛数量不应变化
    if switch_type == 'switch':
        if len(components_after) != len(components_before):
            return False
    
    # 检查是否有电源节点在每个连通分量中
    for component in components_after:
        has_source = any(node in source_nodes for node in component)
        has_transformer = any(node in load_nodes for node in component)
        if not has_source and has_transformer:  # 变压器所在的分量必须有电源
            return False
    
    return True


def calculate_priority(u: str, v: str, switch_type: str, last_operated_nodes: set, action: str) -> int:
    """
    计算操作优先级：
    - 与上一步操作存在共同节点的闸刀：优先级3
    - 任意闸刀：优先级2
    - 闭合断路器：优先级1
    - 拉开断路器：优先级0
    """    
    if switch_type == 'switch':  # 闸刀
        if u in last_operated_nodes or v in last_operated_nodes:
            return 3  # 与上一步操作存在共同节点的闸刀
        else:
            return 2  # 任意闸刀
    else:  # 断路器
        if action == 'close':
            return 1  
        else:
            return 0  


def create_operation_ticket(operation: dict, substations: dict = None) -> dict:
    """
    创建单个操作票
    """
    action_type = "合上" if operation['action'] == 'close' else "分开"
    switch_name = operation['switch_name']
    
    # 根据开关名称和变电站信息确定unit
    unit = "调度中心"  # 默认值
    
    if substations:
        # 通过substations字典查找开关所属的变电站
        station_name = None
        for substation_name, substation_data in substations.items():
            if switch_name in substation_data.get('switches', {}):
                station_name = substation_name
                break
        
        # 根据unit规则确定unit
        if station_name and '变电所' not in station_name:
            # 查找相关线路的owner
            line_owner = None
            for substation_name, substation_data in substations.items():
                for line_name, line_data in substation_data.get('zone_lines', {}).items():
                    if switch_name in line_data.get("breakers", {}).values():
                        line_owner = line_data.get('owner', '') + "集控"
                        break
                if line_owner:
                    break
            unit = line_owner if line_owner else station_name
        else:
            unit = '浙江集控'
    
    return {
        "unit": unit,
        "operation": f"{action_type}{switch_name}",
        "action": "close" if operation['action'] == 'close' else "open",
        "switch_name": switch_name,
        "priority": operation.get('priority', 1)
    }


def solve_dynamic_recovery_model(
    # --- 输入参数 ---
    horizon: int,
    # 区域与负荷
    zones: dict,
    # 拓扑
    substations: dict,
    # 发电与储能
    operating_units: dict,
    backup_units: dict, # 包含启动成本
    hydro_units: dict,
    storage_units: dict,
    interruptible_loads: dict,
    # 优化目标
    objective: ObjectiveType,
    load_enlarge_factor: float,
):
    """
    求解一个完整的多层级、基于连通性推断的电网负荷转移优化问题。
    此函数接收所有参数（包括开关成本），并返回一个包含结果的字典。
    """
    # 数据校验，确保开关、节点、变压器、线路名称唯一性
    # switch_names,node_names,transformer_names,zone_line_names = set(),set(),set(),set()   
    # for sub_name, sub_data in substations.items():
    #     for switch_name in sub_data['switches']:
    #         if switch_name in switch_names:
    #             raise ValueError(f"Switch name {switch_name} is not unique")
    #         switch_names.add(switch_name)
    #     for node_name in sub_data['nodes']:
    #         if node_name in node_names:         
    #             raise ValueError(f"Node name {node_name} is not unique")
    #         node_names.add(node_name)
    #     for transformer_name in sub_data.get('transformers', {}):
    #         if transformer_name in transformer_names:
    #             raise ValueError(f"Transformer name {transformer_name} is not unique")
    #         transformer_names.add(transformer_name) 
    #     for zone_line_name in sub_data.get('zone_lines', {}):
    #         if zone_line_name in zone_line_names:
    #             raise ValueError(f"Zone line name {zone_line_name} is not unique")
    #         zone_line_names.add(zone_line_name)
    # 从substations中提取数据，同时保留变电站映射关系
    transformers = {}
    zone_lines = {}
    switches = {}
    substation_nodes = []
    # 保存开关到变电站的映射关系
    switch_to_substation = {}
    for sub_name, sub_data in substations.items():
        # 提取节点
        substation_nodes.extend(sub_data['nodes'])
        # 提取开关并记录映射关系
        for switch_name, switch_data in sub_data['switches'].items():
            switches[switch_name] = switch_data
            switch_to_substation[switch_name] = sub_name
        # 提取变压器
        transformers.update(sub_data.get('transformers', {}))
        # 提取供区线路
        zone_lines.update(sub_data.get('zone_lines', {}))
    # 过滤掉不可用的储能
    storage_units = {es: p for es, p in storage_units.items() if p["available"]}
    # for u,v in switches.items():
    #     if v["nodes"][0] not in node_names or v["nodes"][1] not in node_names:
    #         print(f"Switch {u} is not connected to valid nodes")

    model = Model("Hybrid_Connectivity_Inference_Transfer_With_Cost")

    # 参数和变量创建部分保持不变...
    # =================================================================================
    # 1. 参数定义
    # =================================================================================
    C = len(transformers)
    M = len(zones)
    # 从新的Switch类结构中提取信息
    initial_sw_states = {name: sw["initial_state"] for name, sw in switches.items()}
    switch_costs = {name: sw["cost"] for name, sw in switches.items()}
    switch_availability = {name: sw.get("available", True) for name, sw in switches.items()}
    # =================================================================================
    # 2. 变量创建
    # =================================================================================
    S = {name: model.addVar(vtype="B", name=f"S_{name}") for name in switches}
    ops_sw = {name: model.addVar(vtype="B", name=f"op_sw_{name}") for name in S}
    # 变电站指示变量：如果为1表示在该变电站进行操作，为0表示不在该变电站操作
    substation_indicator = {sub_name: model.addVar(vtype="B", name=f"sub_indicator_{sub_name}") for sub_name in substations}
    y = { (t_name, z_name): model.addVar(vtype="B", name=f"y_{t_name}_{z_name}") for t_name in transformers for z_name in zones}
    directed_edges = []
    for name, sw in switches.items(): 
        u, v = sw["nodes"]
        directed_edges.extend([(u,v), (v,u)])
    for line_name, line_params in zone_lines.items():
        directed_edges.append((line_params['conn_node'], line_params['zone']))
        directed_edges.append((line_params['zone'], line_params['conn_node']))
    f = { (u, v, z_name): model.addVar(vtype="C", lb=0, name=f"f_{u}_{v}_{z_name}") for u, v in directed_edges for z_name in zones}
    is_energized_by = {n: model.addVar(vtype="C", lb=0, name=f"is_energized_by_{n}") for n in substation_nodes}
    # b) 发电出力变量
    P_opt = { (g, t): model.addVar(vtype="C", lb=0, ub=p['p_max'] - p['p_current'], name=f"P_opt_{g}_{t}") for g, p in operating_units.items() for t in range(horizon) }
    P_bak = { (g, t): model.addVar(vtype="C", lb=0, ub=p['p_max'] if p.get('available', True) else 0, name=f"P_bak_{g}_{t}") for g, p in backup_units.items() for t in range(horizon) }
    P_hydro = { (g, t): model.addVar(vtype="C", lb=0, ub=p['p_max'] if p.get('available', True) else 0, name=f"P_hydro_{g}_{t}") for g, p in hydro_units.items() for t in range(horizon) }
    # 备用机组运行状态决策变量
    v_bak_startup = { (g,t): model.addVar(vtype="B", name=f"v_bak_startup_{g}_{t}") for g in backup_units for t in range(horizon)} 
    v_bak_operating = { (g,t): model.addVar(vtype="B", name=f"v_bak_operating_{g}_{t}") for g in backup_units for t in range(horizon)} 
    # d) 储能变量 (简化为单功率变量)
    P_storage = { (es, t): model.addVar(vtype="C", lb=-p['p_charge_max'] - p['p_current'], ub=p['p_discharge_max'] - p['p_current'], name=f"P_storage_{es}_{t}") for es, p in storage_units.items() for t in range(horizon) }
    SOC = { (es, t): model.addVar(vtype="C", lb=p['soc_min'], ub=p['soc_max'], name=f"SOC_{es}_{t}") for es, p in storage_units.items() for t in range(horizon) }
    SOC_penalty = {es: model.addVar(vtype="C", lb=0, name=f"SOC_penalty_{es}") for es in storage_units}
    
    # 可中断负荷
    P_shed = { (il,t): model.addVar(vtype="C", lb=0, ub=p['shed_max'], name=f"P_shed_{il}_{t}") for il, p in interruptible_loads.items() for t in range(horizon)}


    # 约束部分保持不变...
    # =================================================================================
    # 3. 约束添加
    # =================================================================================
    # a) 流量-开关关联约束
    for s_name, sw in switches.items():
        u, v = sw["nodes"]
        for z_name in zones:
            model.addCons(f[u, v, z_name] + f[v, u, z_name] <= C * S[s_name])
    for line_name, line_params in zone_lines.items(): #单条联络线不能带2变
        for s_name, sw in switches.items():
             u, v = sw["nodes"]
             for z_name in zones:
                if u == line_params['conn_node'] or v == line_params['conn_node']:
                    model.addCons(f[u, v, z_name] + f[v, u, z_name] <= 2)
    # b) 流量守恒约束 
    for n in substation_nodes + list(zones.keys()):
        for z_name in zones:
            in_flow = quicksum(f[m, n, z_name] for m, k in directed_edges if k == n)
            out_flow = quicksum(f[n, k, z_name] for m, k in directed_edges if m == n)
            supply, demand = 0, 0
            
            if n in zones.keys() and z_name == n:
                supply += quicksum(y[t, z_name] for t in transformers)

            for t_name, t_params in transformers.items():
                if t_params['conn_node'] == n:
                    demand += y[t_name, z_name]

            model.addCons(out_flow - in_flow == supply - demand)

    # c) 负荷归属和连通性约束
    for t_name in transformers:
        if max(abs(x) for x in transformers[t_name]["load"]) > 0.5:
            model.addCons(quicksum(y[t_name, z_name] for z_name in zones) == 1)
        t_conn_node = transformers[t_name]['conn_node']
        for z_name in zones:
            in_flow_to_t = quicksum(f[m, t_conn_node, z_name] for m, k in directed_edges if k == t_conn_node)
            model.addCons(in_flow_to_t >= y[t_name, z_name])
        if transformers[t_name]['allocate']:
            z_name = transformers[t_name]['allocate']
            model.addCons(y[t_name, z_name] == 1)

    # d) 分区解环运行约束
    for line_name, line_params in zone_lines.items():
        line_conn_node = line_params['conn_node']
        for zone_idx, zone in enumerate(zones.keys()):
            if zone == line_params['zone']:
                model.addCons(is_energized_by[line_conn_node] == zone_idx)
    for s_name, sw in switches.items():
        u, v = sw["nodes"]
        s_var = S[s_name]
        model.addCons(is_energized_by[u] - is_energized_by[v] <= M * (1 - S[s_name]))
        model.addCons(is_energized_by[u] - is_energized_by[v] >= - M * (1 - S[s_name]))
    # d) 供区线路流量约束：线路相关开关在非目标供区中的流量为0
    for line_name, line_params in zone_lines.items():
        target_zone = line_params['zone']
        line_related_switches = set()
        
        # 获取该线路的local和remote breaker
        breakers = line_params.get('breakers', {})
        local_breaker = breakers.get('local')
        remote_breaker = breakers.get('remote')
        
        # 收集线路相关的开关
        line_related_switches.add(local_breaker)
        line_related_switches.add(remote_breaker)
        
        # 找到与这些breaker共享节点的其他开关
        breaker_nodes = set()
        for breaker_name in [local_breaker, remote_breaker]:
            breaker_nodes.update(switches[breaker_name]['nodes'])
        
        # 查找与breaker共享节点的其他开关
        for switch_name, switch_data in switches.items():
            switch_nodes = set(switch_data['nodes'])
            if switch_nodes & breaker_nodes:  # 如果有共同节点
                line_related_switches.add(switch_name)
        
        # 为线路相关开关在非目标供区中的流量设置为0
        for switch_name in line_related_switches:
            print(switch_name)
            u, v = switches[switch_name]['nodes']
            for z_name in zones:
                if z_name != target_zone:
                    model.chgVarLb(f[u, v, z_name], 0)
                    model.chgVarLb(f[v, u, z_name], 0)
                    model.chgVarUb(f[u, v, z_name], 0)
                    model.chgVarUb(f[v, u, z_name], 0)

    # e) 变电站开关流量约束：每个变电站内的开关流量只能来自该变电站内zone_lines对应的zone集合
    for substation_name, substation_data in substations.items():
        # 获取该变电站内zone_lines对应的zone集合
        substation_zones = set()
        for line_name, line_params in substation_data.get('zone_lines', {}).items():
            substation_zones.add(line_params['zone'])
        
        # 为该变电站内的所有开关设置流量约束
        for switch_name, switch_data in substation_data.get('switches', {}).items():
            u, v = switch_data['nodes']
            # 对于不在该变电站zone集合中的zone，将流量设置为0
            for z_name in zones:
                if z_name not in substation_zones:
                    model.chgVarLb(f[u, v, z_name], 0)
                    model.chgVarLb(f[v, u, z_name], 0)
                    model.chgVarUb(f[u, v, z_name], 0)
                    model.chgVarUb(f[v, u, z_name], 0)

    # f) 供区容量约束
    safety_region = {(name,t): model.addVar(vtype="C", lb=0, ub=zones[name]['capacity'], name=f"safety_region_{name}_{t}") for name in zones for t in range(horizon)}
    min_safety_region = model.addVar(vtype="C", lb=0, ub=1, name="min_safety_region")
    # b) 系统功率平衡约束,供区充裕度约束
    for t in range(horizon):
        for z_name, z_params in zones.items():
            supply_side = (quicksum(P_opt[g, t] * operating_units[g]['sensitivity'] for g, p in operating_units.items() if p['zone'] == z_name) +
                           quicksum(P_bak[g, t] * backup_units[g]['sensitivity'] for g, p in backup_units.items() if p['zone'] == z_name) +
                           quicksum(P_hydro[g, t] * hydro_units[g]['sensitivity'] for g, p in hydro_units.items() if p['zone'] == z_name) +
                           quicksum(P_storage[es, t] * storage_units[es]['sensitivity'] for es, p in storage_units.items() if p['zone'] == z_name))
            enlarged_fixed_load = load_enlarge_factor * z_params['fixed_load'][t] + (load_enlarge_factor - 1)*sum(operating_units[g]['p_current'] for g, p in operating_units.items() if p['zone'] == z_name)
            demand_side = (enlarged_fixed_load +
                           quicksum(transformers[t_name]['load'][t] * y[t_name, z_name] * transformers[t_name]['sensitivity'][z_name] for t_name in transformers) - \
                           quicksum(P_shed[il,t] for il, p in interruptible_loads.items() if p['zone'] == z_name))
            model.addCons(demand_side + safety_region[z_name,t] == supply_side + z_params['capacity'])
            model.addCons(min_safety_region <= safety_region[z_name,t]/z_params['capacity'])

    # g) 备用机组启动延迟约束,启动一小时后并网，并网一小时后带满，开机之后不停机
    for g, p in backup_units.items():
        for t in range(horizon):
            model.addCons(v_bak_startup[g,t] + v_bak_operating[g,t] <= 1)
            if t >= 1:
                model.addCons(v_bak_operating[g,t] >= v_bak_operating[g,t - 1])
            # 如果备用机组不可用，则启动变量固定为0
            if not p.get('available', True):
                model.addCons(v_bak_startup[g,t] == 0)
                model.addCons(v_bak_operating[g,t] == 0)
                model.addCons(P_bak[g,t] == 0)
            elif t == 0:
                model.addCons(v_bak_operating[g,t] == 0)
                model.addCons(P_bak[g,t] == 0)
            else:
                model.addCons(P_bak[g, t] == v_bak_startup[g, t - 1] * p['p_min'] + v_bak_operating[g, t - 1] * p['p_max'])
                model.addCons(v_bak_startup[g,t - 1] + v_bak_operating[g,t - 1] == v_bak_operating[g,t])

    # 如果进行了倒排，那么运行机组需要出力加满，水电应该出力加满，至少开一台备用机组
    operation_on = model.addVar(vtype="B", name="operation_on")
    for switch_name, switch_params in switches.items():
        model.addCons(operation_on >= ops_sw[switch_name])
    if backup_units:
        model.addCons(quicksum(v_bak_operating[g, horizon-1] for g, p in backup_units.items()) >= operation_on)
    for g,p in operating_units.items():
        for t in range(horizon):
            model.addCons(P_opt[g, t] + p['p_current'] >= p['p_max'] * operation_on)
    for g,p in hydro_units.items():
        for t in range(horizon):
            model.addCons(P_hydro[g, t] >= p['p_max'] * operation_on)
    # g) 储能SOC动态约束
    for es, p in storage_units.items():
        # 初始SOC
        model.addCons(SOC[es, 0] == p['soc_initial']) # 假设时间步长为1小时
        # 时序SOC
        for t in range(1, horizon):
            model.addCons(SOC[es, t] == SOC[es, t-1] - P_storage[es, t] * 1)
        # SOC 惩罚
        model.addCons(SOC_penalty[es] >= p['soc_max'] * 0.5 - SOC[es, horizon-1])
            
    # h) 隔离开关-断路器耦合约束
    for s_name, sw in switches.items():
        if sw['switch_type'] == 'breaker':
            u, v = sw["nodes"]
            breaker_initial_state = initial_sw_states[s_name]
            breaker_final_state = S[s_name]
            
            # 找到与此breaker相连的所有switch
            connected_switches_on_u = []
            connected_switches_on_v = []
            for other_name, other_sw in switches.items():
                if other_name != s_name and other_sw['switch_type'] == 'switch':
                    other_u, other_v = other_sw["nodes"]
                    # 检查是否有共同节点
                    if u in [other_u, other_v]:
                        connected_switches_on_u.append(other_name)
                    elif v in [other_u, other_v]:
                        connected_switches_on_v.append(other_name)
            
            # 添加约束条件, 确保最终状态在运行、热备用、冷备用三者之间
            if connected_switches_on_u:
                model.addCons(quicksum(S[sw_name] for sw_name in connected_switches_on_u) <= 1)
                model.addCons(quicksum(S[sw_name] for sw_name in connected_switches_on_u) >= breaker_final_state)
            if connected_switches_on_v:
                model.addCons(quicksum(S[sw_name] for sw_name in connected_switches_on_v) <= 1)
                model.addCons(quicksum(S[sw_name] for sw_name in connected_switches_on_v) >= breaker_final_state)
    # i) 不破坏网架，结束时的开关闭合数大于等于初始状态，并赋予超出值3.0的奖励系数
    rho = model.addVar(vtype="C", name="rho",lb=-1,ub=10) # 超出值
    model.addCons(quicksum(S[name] for name in switches) - rho >= sum(initial_sw_states.values()))
    # 3.5. 可用性约束
    # =================================================================================
    # 获取不可用区域线路相连的节点
    unavailable_zone_line_nodes = set()
    for line_name, line_params in zone_lines.items():
        if not line_params.get('available', True):
            unavailable_zone_line_nodes.add(line_params['conn_node'])
    
    # 开关可用性约束
    for name, sw in switches.items():
        u, v = sw["nodes"]
        # 如果开关不可用，或者连接到不可用区域线路的节点，则状态固定为初始状态
        if not switch_availability[name] or u in unavailable_zone_line_nodes or v in unavailable_zone_line_nodes:
            model.addCons(S[name] == initial_sw_states[name])
    
    # 定义开关操作变量
    for name, s_var in S.items():
        if initial_sw_states.get(name, 0) == 0: 
            model.addCons(ops_sw[name] >= s_var)
        else: 
            model.addCons(ops_sw[name] >= 1 - s_var)
    
    # 变电站指示变量约束：如果变电站指示变量为0，则该变电站内所有开关操作变量都为0
    for switch_name, sub_name in switch_to_substation.items():
        if switch_name in ops_sw:
            model.addCons(ops_sw[switch_name] <= substation_indicator[sub_name])

    # 运行成本
    op_cost = quicksum(p['cost'] * (P_opt[g, t] + operating_units[g]['p_current']) for g,p in operating_units.items() for t in range(horizon)) + \
               quicksum(p['cost'] * P_bak[g, t] for g,p in backup_units.items() for t in range(horizon)) + \
               quicksum(p['cost'] * P_hydro[g, t] for g,p in hydro_units.items() for t in range(horizon)) + \
               quicksum(p['startup_cost'] * v_bak_startup[g, t] for g, p in backup_units.items() for t in range(horizon)) + \
               quicksum(transformers[t_name]['load'][t] * y[t_name, z_name] * transformers[t_name]['sensitivity'][z_name] * transformers[t_name]['cost'][z_name] for t_name in transformers for z_name in zones)
    load_shedding_cost = quicksum(p['cost'] * P_shed[il, t] for il, p in interruptible_loads.items() for t in range(horizon))
    #潜在的失负荷
    load_shedding_cost += quicksum(SOC_penalty[es] * 100 for es in storage_units)
    # 变电站操作成本
    substation_operation_cost = quicksum(substation_indicator[sub_name] * sub_params['operation_cost'] for sub_name, sub_params in substations.items())
    # substation_operation_cost = 0
    # 根据目标类型设置单一目标函数（3选1）
    print(f"Optimization objective: {objective}")
    eps = 1e-4
    max_cost = max([p['cost']*p['p_max'] for p in operating_units.values()]) if operating_units else 1000
    obj_expr = eps * (quicksum(ops_sw[name] * switch_costs.get(name, 1.0) for name in S) - min_safety_region + op_cost/max_cost + substation_operation_cost)
    if objective == ObjectiveType.MIN_SWITCH_OP:
        # 最小化开关操作成本 + 变电站操作成本
        obj_expr += quicksum(ops_sw[name] * switch_costs.get(name, 1.0) for name in S) + substation_operation_cost
    elif objective == ObjectiveType.MAX_SAFETY_REGION:
        # 最大化安全裕度（转换为最小化负的安全裕度）
        obj_expr += -min_safety_region  # 将比值转化为百分数
    elif objective == ObjectiveType.MIN_COST:
        # 最小化发电成本
        obj_expr += op_cost
    obj_expr += load_shedding_cost
    obj_expr += - 3.0 * rho
    model.setObjective(obj_expr, "minimize")
    # =================================================================================
    # 5. 求解与结果封装 (更新返回的字典)
    # =================================================================================

    model.optimize()
    # model.solveConcurrent()

    
    if model.getStatus() == "optimal":
        final_switch_states = {name: round(model.getVal(var)) for name, var in S.items()}
        switch_operations = []
        op_count = 0
        for name, initial_state in initial_sw_states.items():
            final_state = final_switch_states[name]
            if initial_state != final_state:
                op_count +=1
                action = "close" if final_state == 1 else "open"
                switch_operations.append({
                    "switch_name": name,
                    "initial_state": initial_state,
                    "final_state": final_state,
                    "action": action,
                    "cost": switch_costs.get(name, 1.0) # 在结果中也返回成本
                })
        final_transformer_assignment = {}
        for t_name, t_params in transformers.items():
            if "储能" in t_name:
                continue
            assigned_zone = "停役"
            for z_name in zones:
                if model.getVal(y[t_name, z_name]) > 0.5:
                    assigned_zone = z_name
                    break
            final_transformer_assignment[t_name] = {
                "assigned_zone": assigned_zone, "load": t_params['load']
            }
        final_zone_status = {}
        for z_name, z_params in zones.items():
            capacity = z_params['capacity']
            load = [round(capacity - model.getVal(safety_region[z_name,t]),2) for t in range(horizon)]
            final_zone_status[z_name] = {
                "final_load": load,
                "capacity": capacity,
                "status": "安全" if max(load) <= capacity else "过载!",
                "safety_region_percent": [round(model.getVal(safety_region[z_name,t])/capacity *100, 2) for t in range(horizon)]
            }
        final_dispatch_plan = []
        for t in range(horizon):
            hourly_plan = {
                "time": (datetime.now() + timedelta(hours=t)).strftime("%H:%M"), # to string
                "generation": {},
                "storage": {},
                "shedding": {}
            }
            for g in operating_units: hourly_plan["generation"][g] = round(model.getVal(P_opt[g, t]) + operating_units[g]['p_current'], 2)
            for g in backup_units: hourly_plan["generation"][g] = round(model.getVal(P_bak[g, t]), 2)
            for g in hydro_units: hourly_plan["generation"][g] = round(model.getVal(P_hydro[g, t]), 2)
            for es in storage_units: 
                hourly_plan["storage"][es] = {
                    "power_mw": round(model.getVal(P_storage[es, t]) + storage_units[es]['p_current'], 2),
                    "soc_mwh": round(model.getVal(SOC[es, t]), 2)
                }
            for il in interruptible_loads: hourly_plan["shedding"][il] = round(model.getVal(P_shed[il, t]), 2)
            final_dispatch_plan.append(hourly_plan)
        # 备用机组开机情况
        backup_unit_commitment = ""
        for g in backup_units:
            for t in range(horizon):
                if model.getVal(v_bak_startup[g,t]) == 1:
                    backup_unit_commitment += f"{g}在时段{(datetime.now() + timedelta(hours=t)).strftime('%H:%M')}开机\n"
                    break
        
        # 变电站操作信息
        substation_operations = []
        total_substation_cost = 0
        for sub_name, sub_params in substations.items():
            if model.getVal(substation_indicator[sub_name]) > 0.5:
                operated_switches = [sw_name for sw_name in sub_params['switches'] if any(op['switch_name'] == sw_name for op in switch_operations)]
                substation_operations.append({
                    "substation_name": sub_name,
                    "st_id": sub_params['st_id'],
                    "operation_cost": sub_params['operation_cost'],
                    "switches_operated": operated_switches
                })
                total_substation_cost += sub_params['operation_cost']
        line_operations = set()
        for operation in switch_operations:
            for line_params in zone_lines.values():
                if operation['switch_name'] in line_params['breakers'].values():
                    line_operations.add(line_params["line_id"])
        line_operations = list(line_operations)
        source_node,load_nodes = set(),set()
        for _, line_params in zone_lines.items():
            source_node.add(line_params['conn_node'])
        for _, trans_param in transformers.items():
            if max(abs(x) for x in trans_param["load"]) > 0:
                load_nodes.add(trans_param['conn_node'])
        print(switch_operations)
        tickets = generate_tickets(switch_operations, switches, source_node, load_nodes, substations)
        result = {
            "status": "Optimal Solution Found",
            "objective_value": round(model.getObjVal(), 4),
            # --- 更新此处，反映成本 ---
            "summary": {
                "operation_cost": round(model.getVal(op_cost), 4),
                "safety_region_percent": round(model.getVal(min_safety_region)*100, 2),
                "total_operations_count": op_count,
                "substations_operated_count": len(substation_operations),
                "total_substation_cost": total_substation_cost
            },
            "results": {
                "调度时段": [f"{(datetime.now() + timedelta(hours=t)).strftime('%H:%M')}" for t in range(horizon)],
                "可用资源":{
                    "在运机组": operating_units,
                    "备用机组": backup_units,
                    "储能": storage_units,
                    "可中断负荷": interruptible_loads
                },
                "备用机组开机计划": backup_unit_commitment,
                "机组出力计划": final_dispatch_plan,
                "开关操作": switch_operations,
                "待操作变电站": substation_operations,
                "待操作线路": line_operations,
                "变压器所属供区": final_transformer_assignment,
                "供区供电裕度": final_zone_status,
                "操作票": tickets,
            }
        }
        json.dump(result, open("example/tmp.json", "w"), ensure_ascii=False, indent=4)
        return result
    else:
        return None

if __name__ == "__main__":
    # Load the JSON data (in this case, we'll use the provided dictionary)
    # with open("example/export.json", "r", encoding='utf-8') as f:
    #     json_data = json.load(f)
    from main import get_optimization_boundary
    json_data = get_optimization_boundary(device_name="双龙变1号主变",device_type="主变",load_enlarge_factor=1)
    with open("example/export.json", "w", encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=4)
    input = OptimizationInput(**json_data)
    params = input.model_dump()
    result = solve_dynamic_recovery_model(**params)
    with open("example/tmp.json", "w", encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=4)
    from utils import save_tickets_to_excel
    save_tickets_to_excel({
        "tickets": result['results']['操作票'],
        "description": "故障恢复操作票"
    })
    print(result)