# optimization_solver.py
from pyscipopt import Model, quicksum
from schema import ObjectiveType, OptimizationInput
from datetime import datetime, timedelta
from topology_analysis import build_power_system_graph, get_connected_edges_with_attrs
import json

def solve_dynamic_recovery_model(
    # --- 输入参数 ---
    horizon: int,
    # 区域与负荷
    zones: dict,
    zone_lines: dict,
    transformers: dict, # 包含时序负荷和供电成本
    # 拓扑
    substation_nodes: list,
    switches: dict,
    # 发电与储能
    operating_units: dict,
    backup_units: dict, # 包含启动成本
    hydro_units: dict,
    storage_units: dict,
    interruptible_loads: dict,
    # 优化目标
    objective: ObjectiveType
):
    """
    求解一个完整的多层级、基于连通性推断的电网负荷转移优化问题。
    此函数接收所有参数（包括开关成本），并返回一个包含结果的字典。
    """
    # # print all input
    # print("horizon: ", horizon)
    # print("zones: ", zones)
    # print("zone_lines: ", zone_lines)
    # print("transformers: ", transformers)
    # print("substation_nodes: ", substation_nodes)
    # print("switches: ", switches)
    # print("operating_units: ", operating_units)
    # print("backup_units: ", backup_units)
    # print("hydro_units: ", hydro_units)
    # print("storage_units: ", storage_units)
    # print("interruptible_loads: ", interruptible_loads)
    # print("objective: ", objective)
    model = Model("Hybrid_Connectivity_Inference_Transfer_With_Cost")

    # 参数和变量创建部分保持不变...
    # =================================================================================
    # 1. 参数定义
    # =================================================================================
    C = len(transformers)
    M = C + 1
    # 从新的Switch类结构中提取信息
    initial_sw_states = {name: sw["initial_state"] for name, sw in switches.items()}
    switch_costs = {name: sw["cost"] for name, sw in switches.items()}
    switch_availability = {name: sw.get("available", True) for name, sw in switches.items()}
    # =================================================================================
    # 2. 变量创建
    # =================================================================================
    is_energized_by = {n: model.addVar(vtype="I", lb=0, ub=len(zones), name=f"is_energized_by_{n}") for n in substation_nodes}
    S = {name: model.addVar(vtype="B", name=f"S_{name}") for name in switches}
    ops_sw = {name: model.addVar(vtype="B", name=f"op_sw_{name}") for name in S}
    y = { (t_name, z_name): model.addVar(vtype="B", name=f"y_{t_name}_{z_name}") for t_name in transformers for z_name in zones}
    directed_edges = []
    for name, sw in switches.items(): 
        u, v = sw["nodes"]
        directed_edges.extend([(u,v), (v,u)])
    for line_name, line_params in zone_lines.items():
        directed_edges.append((line_params['conn_node'], line_params['zone']))
        directed_edges.append((line_params['zone'], line_params['conn_node']))
    f = { (u, v, z_name): model.addVar(vtype="C", lb=0, name=f"f_{u}_{v}_{z_name}") for u, v in directed_edges for z_name in zones}

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
                    model.addCons(f[u, v, z_name] + f[v, u, z_name] <= 1.5)
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
        if max(transformers[t_name]['load']) > 0:
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

    # e) 供区容量约束
    safety_region = {(name,t): model.addVar(vtype="C", lb=0, name=f"safety_region_{name}_{t}") for name in zones for t in range(horizon)}
    min_safety_region = model.addVar(vtype="C", name="min_safety_region")
    # b) 系统功率平衡约束,供区充裕度约束
    for t in range(horizon):
        for z_name, z_params in zones.items():
            supply_side = (quicksum((P_opt[g, t] + operating_units[g]['p_current']) * operating_units[g]['sensitivity'] for g, p in operating_units.items() if p['zone'] == z_name) +
                           quicksum(P_bak[g, t] * backup_units[g]['sensitivity'] for g, p in backup_units.items() if p['zone'] == z_name) +
                           quicksum(P_hydro[g, t] * hydro_units[g]['sensitivity'] for g, p in hydro_units.items() if p['zone'] == z_name) +
                           quicksum((P_storage[es, t] + storage_units[es]['p_current']) * storage_units[es]['sensitivity'] for es, p in storage_units.items() if p['zone'] == z_name))
            demand_side = (z_params['fixed_load'][t] +
                           quicksum(transformers[t_name]['load'][t] * y[t_name, z_name] * transformers[t_name]['sensitivity'][z_name] for t_name in transformers) - \
                           quicksum(P_shed[il,t] for il, p in interruptible_loads.items() if p['zone'] == z_name))
            model.addCons( demand_side + safety_region[z_name,t] == supply_side + z_params['capacity'])
            model.addCons(min_safety_region <= safety_region[z_name,t]/z_params['capacity'])

    # f) 备用机组启动延迟约束,启动一小时后并网，并网一小时后带满，开机之后不停机
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

    # g) 储能SOC动态约束
    for es, p in storage_units.items():
        # 初始SOC
        model.addCons(SOC[es, 0] == p['soc_initial']) # 假设时间步长为1小时
        # 时序SOC
        for t in range(1, horizon):
            model.addCons(SOC[es, t] == SOC[es, t-1] - P_storage[es, t] * 1)
            
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
                model.addCons(quicksum(S[sw_name] for sw_name in connected_switches_on_u) >= breaker_final_state)
            if connected_switches_on_v:
                model.addCons(quicksum(S[sw_name] for sw_name in connected_switches_on_v) >= breaker_final_state)
    # i) 不破坏网架，结束时的开关闭合数大于等于初始状态
    model.addCons(quicksum(S[name] for name in switches) >= sum(initial_sw_states.values()))
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

    # 新目标：最小化加权的操作总成本
    op_cost = quicksum(p['cost'] * (P_opt[g, t] + operating_units[g]['p_current']) for g,p in operating_units.items() for t in range(horizon)) + \
               quicksum(p['cost'] * P_bak[g, t] for g,p in backup_units.items() for t in range(horizon)) + \
               quicksum(p['cost'] * P_hydro[g, t] for g,p in hydro_units.items() for t in range(horizon)) + \
               quicksum(p['startup_cost'] * v_bak_startup[g, t] for g, p in backup_units.items()) + \
               quicksum(transformers[t_name]['load'][t] * y[t_name, z_name] * transformers[t_name]['sensitivity'][z_name] * transformers[t_name]['cost'][z_name] for t_name in transformers for z_name in zones)
    load_shedding_cost = quicksum(p['cost'] * P_shed[il, t] for il, p in interruptible_loads.items() for t in range(horizon))
    # 根据目标类型设置单一目标函数（3选1）
    print(f"Optimization objective: {objective}")
    eps = 1e-4
    obj_expr = eps * (quicksum(ops_sw[name] * switch_costs.get(name, 1.0) for name in S) - min_safety_region + op_cost/max([p['cost']*p['p_max'] for p in operating_units.values()]))
    if objective == ObjectiveType.MIN_SWITCH_OP:
        # 最小化开关操作成本
        obj_expr += quicksum(ops_sw[name] * switch_costs.get(name, 1.0) for name in S)
    elif objective == ObjectiveType.MAX_SAFETY_REGION:
        # 最大化安全裕度（转换为最小化负的安全裕度）
        obj_expr += -min_safety_region  # 将比值转化为百分数
    elif objective == ObjectiveType.MIN_COST:
        # 最小化发电成本
        obj_expr += op_cost
    obj_expr += load_shedding_cost
    model.setObjective(obj_expr, "minimize")
    # =================================================================================
    # 5. 求解与结果封装 (更新返回的字典)
    # =================================================================================
    model.optimize()
    
    if model.getStatus() == "optimal":
        final_switch_states = {name: round(model.getVal(var)) for name, var in S.items()}
        
        switch_operations = []
        op_count = 0
        for name, initial_state in initial_sw_states.items():
            final_state = final_switch_states[name]
            if initial_state != final_state:
                op_count +=1
                action = "合闸 (Close)" if final_state == 1 else "分闸 (Open)"
                switch_operations.append({
                    "switch_name": name,
                    "initial_state": initial_state,
                    "final_state": final_state,
                    "action": action,
                    "cost": switch_costs.get(name, 1.0) # 在结果中也返回成本
                })
        final_transformer_assignment = {}
        for t_name, t_params in transformers.items():
            assigned_zone = "失电"
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
        # 生成开关刀闸操作顺序
        power_graph = build_power_system_graph(substation_nodes, switches)
        operations = []
        edges = {}
        breakers_operate = {}
        switches_operate = {}
        for edge in list(power_graph.edges(data=True)):
            edge_name = edge[2]['switch_name']
            edges[edge_name] = edge
            if edge[2]['switch_type'] == 'breaker':
                # 无需操作
                if final_switch_states[edge_name] == edge[2]['initial_state']:
                    breakers_operate[edge_name] = 0
                # 由分到合
                elif final_switch_states[edge_name] == 1 and edge[2]['initial_state'] == 0:
                    breakers_operate[edge_name] = 1
                # 由合到分
                elif final_switch_states[edge_name] == 0 and edge[2]['initial_state'] == 1:
                    breakers_operate[edge_name] = 2
            elif edge[2]['switch_type'] == 'switch':
                # 无需操作
                if final_switch_states[edge_name] == edge[2]['initial_state']:
                    switches_operate[edge_name] = 0
                # 由分到合
                elif final_switch_states[edge_name] == 1 and edge[2]['initial_state'] == 0:
                    switches_operate[edge_name] = 1
                # 由合到分
                elif final_switch_states[edge_name] == 0 and edge[2]['initial_state'] == 1:
                    switches_operate[edge_name] = 2
        # 先操作开关
        for breaker_name, operate in breakers_operate.items():
            if operate == 1:
                # 找到需要分闸的开关，应与合闸开关两端的连通子图相连
                close_conn_graph = edges[breaker_name][2]['connected_components']
                open_breaker = "not_find"
                for open_breaker_name, open_operate in breakers_operate.items():
                    if open_operate == 2:
                        open_conn_graph = edges[open_breaker_name][2]['connected_components']
                        if open_conn_graph[0] in close_conn_graph or open_conn_graph[1] in close_conn_graph:
                            open_breaker = open_breaker_name
                    if open_breaker != "not_find":
                        break
                # 合闸操作，若找到与开关相连的需合闸的刀闸，则先合刀闸
                # 获取与开关相连的所有边
                u = edges[breaker_name][0]
                v = edges[breaker_name][1]
                connected_edges = get_connected_edges_with_attrs(power_graph, u, v)
                for edge in connected_edges:
                    if edge[2]['switch_type'] == 'switch':
                        switch_name = edge[2]['switch_name']
                        if switches_operate[switch_name] == 1:
                            operations.append(f"{switch_name}【刀闸合闸】")
                            print(f"1、{switch_name}【刀闸合闸】")
                            switches_operate[switch_name] = 0
                operations.append(f"{breaker_name}【开关合闸】")
                print(f"1、{breaker_name}【开关合闸】")
                breakers_operate[breaker_name] = 0
                # 分闸开关操作
                if open_breaker != "notfind":
                    operations.append(f"{open_breaker}【开关分闸】")
                    print(f"2、{open_breaker}【开关分闸】")
                    breakers_operate[open_breaker] = 0
                    # 若找到与开关相连的需分闸的刀闸，则分刀闸
                    # 获取与开关相连的所有边
                    u = edges[open_breaker][0]
                    v = edges[open_breaker][1]
                    connected_edges = get_connected_edges_with_attrs(power_graph, u, v)
                    for edge in connected_edges:
                        if edge[2]['switch_type'] == 'switch':
                            switch_name = edge[2]['switch_name']
                            if switches_operate[switch_name] == 2:
                                operations.append(f"{switch_name}【刀闸分闸】")
                                print(f"2、{switch_name}【刀闸分闸】")
                                switches_operate[switch_name] = 0
        # 再操作剩余刀闸
        for switch_name, operate in switches_operate.items():
            if operate == 1:
                operations.append(f"{switch_name}【刀闸合闸】")
                print(f"3、{switch_name}【刀闸合闸】")
                switches_operate[switch_name] = 0
                # 找到需要分闸的刀闸，应与合闸刀闸相连
                u = edges[switch_name][0]
                v = edges[switch_name][1]
                connected_edges = get_connected_edges_with_attrs(power_graph, u, v)
                for edge in connected_edges:
                    if edge[2]['switch_type'] == 'switch':
                        switch_name = edge[2]['switch_name']
                        if switches_operate[switch_name] == 2:
                            operations.append(f"{switch_name}【刀闸分闸】")
                            print(f"3、{switch_name}【刀闸分闸】")
                            switches_operate[switch_name] = 0
        result = {
            "status": "Optimal Solution Found",
            "objective_value": round(model.getObjVal(), 4),
            # --- 更新此处，反映成本 ---
            "summary": {
                "operation_cost": round(model.getVal(op_cost), 4),
                "safety_region_percent": round(model.getVal(min_safety_region)*100, 2),
                "total_operations_count": op_count
            },
            "results": {
                "time_slots": [f"{(datetime.now() + timedelta(hours=t)).strftime('%H:%M')}" for t in range(horizon)],
                "switch_operations": switch_operations,
                "final_transformer_assignment": final_transformer_assignment,
                "final_zone_status": final_zone_status,
                "final_switch_states": final_switch_states,
                "initial_sw_states": initial_sw_states,
                "operations": operations,
                "dispatch_plan": final_dispatch_plan
            }
        }
        return result
    else:
        return None

if __name__ == "__main__":
    # Load the JSON data (in this case, we'll use the provided dictionary)
    with open("power_system_test.json", "r", encoding='utf-8') as f:
        json_data = json.load(f)
    input = OptimizationInput(**json_data)
    params = input.model_dump()
    result = solve_dynamic_recovery_model(**params)
    print(result)