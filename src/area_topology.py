import pandapower as pp
import pandapower.topology as top
import pandas as pd
import networkx as nx
import json
from collections import defaultdict

def identify_interconnection_substations(
    net, island_stats, bus_to_island, islands, substation_to_island
):
    """
    识别联络变电站并收集相关设备信息
    联络变电站定义：供区内的变电站中有一条线与别的供区相连
    """
    print("\n开始识别联络变电站...")

    # 创建区域名称到区域索引的映射
    area_to_island_index = {
        area_name: info["island_index"] for area_name, info in island_stats.items()
    }

    # 遍历所有线路，检查是否连接不同的供区
    for line_idx, line in net.line.iterrows():
        from_st,to_st = line.from_st_name, line.to_st_name
        if "变电所" in from_st or "变电所" in to_st:
            continue
        from_island, to_island = substation_to_island[from_st], substation_to_island[to_st]
        # 检查两侧变电站是否在不同的供区，且不存在分列运行
        if from_island != to_island and from_island and to_island:
            # 本侧变电站在不同的供区，记录联络变电站信息
            if "电厂" in from_st or "电厂" in to_st:
                continue
            # print(from_st,to_st)
            # print(substation_to_island[from_st],substation_to_island[to_st])
            # 如果有一侧的island==2，那么另外一侧一定只有一个 island，意味着本侧分列，另一侧并列，本侧同时加到两个供区的资源里。
            if len(from_island) == 2:
                for island in from_island:
                    if from_st not in island_stats[island]["联络变电站"]:
                        island_stats[island]["联络变电站"][from_st] = (
                            collect_substation_equipment(net, from_st, substation_to_island)
                        )
            elif len(to_island) == 2:
                for island in to_island:
                    if to_st not in island_stats[island]["联络变电站"]:
                        island_stats[island]["联络变电站"][to_st] = (
                            collect_substation_equipment(net, to_st, substation_to_island)
                        )
            else:
                for st in [from_st, to_st]:
                    for island in substation_to_island[st]:
                        if st not in island_stats[island]["联络变电站"]:
                            island_stats[island]["联络变电站"][st] = (
                                collect_substation_equipment(net, st, substation_to_island)
                            )
    for island in island_stats.keys():
        zones = set()
        for st in island_stats[island]["联络变电站"]:
            zones = zones.union(set(island_stats[island]["联络变电站"][st]["zones"]))
        island_stats[island]["zones"] = {}
        for zone in zones:
            island_stats[island]["zones"][zone] = {
                "capacity": island_stats[zone]["主变"],
                # "fixed_load":None
            }
        zone_line_count = defaultdict(int)
        # 如果多个变电站出现了同一条zone_line名称，那么把这些线路都移除
        for st in island_stats[island]["联络变电站"]:
            for zone_line in island_stats[island]["联络变电站"][st]["zone_lines"]:
                zone_line_count[zone_line] += 1
        for st in island_stats[island]["联络变电站"]:
            for zone_line in list(island_stats[island]["联络变电站"][st]["zone_lines"].keys()):
                if zone_line_count[zone_line] > 1:
                    del island_stats[island]["联络变电站"][st]["zone_lines"][zone_line]
    return island_stats


def collect_substation_equipment(net, substation_name, substation_to_island):
    """
    收集变电站内的母线、开关、变压器、线路信息
    """
    # 收集变电站内的母线
    buses = net.bus[net.bus.st_name == substation_name]
    # 收集变电站内的开关
    switches = net.switch[net.switch.bus.isin(buses.index)]    
    transformers = net.load[net.load.bus.isin(buses.index)]
    lines = net.line[
        net.line.from_bus.isin(buses.index) | net.line.to_bus.isin(buses.index)
    ]
    equipment = {
        "st_id": net.bus[net.bus.st_name == substation_name].st_id.values[0],
        "nodes": [net.bus.name[bus] for bus in buses.index],  # 母线
        "switches": {},  # 开关
        "transformers": {},  # 变压器
        "zone_lines": {},  # 线路
    }
    for _, switch in switches.iterrows():
        if "待用" in switch["name"]:
            continue
        equipment["switches"][switch["name"]] = {
            "available": True,
            "initial_state": 1 if switch["closed"] else 0,
            "nodes": [net.bus.name[switch["element"]], net.bus.name[switch["bus"]]],
            "switch_type": "switch" if "闸刀" in switch["name"] else "breaker",
        }
        if "闸刀" in switch["name"]:
            equipment["switches"][switch["name"]]["cost"] = 5
        elif "母联" in switch["name"]:
            equipment["switches"][switch["name"]]["cost"] = 3
        else:
            equipment["switches"][switch["name"]]["cost"] = 1
    for _, line in lines.iterrows():
        try:

            # print(line.from_st_name,line.to_st_name)
            # print(substation_to_island[line.from_st_name],substation_to_island[line.to_st_name])
            
            # 确定线路的本侧和对侧母线
            local_bus = line["from_bus"] if line["from_bus"] in buses.index else line["to_bus"]
            remote_bus = line["from_bus"] if line["from_bus"] not in buses.index else line["to_bus"]
            
            # 获取本侧和对侧母线名称
            local_bus_name = net.bus.name[local_bus]
            remote_bus_name = net.bus.name[remote_bus]                
            # 创建以线路名称命名的合并节点
            merged_node_name = line["name"]
            # equipment["nodes"].append(merged_node_name)
            # 查找从虚拟母线b出发，通过开关连接到其他母线的路径
            final_bus_name = merged_node_name  # 使用合并后的节点名称
            
            # 查找连接到对侧母线的闸刀
            connected_switches = net.switch[(net.switch.bus == remote_bus) | (net.switch.element == remote_bus)]
            connected_switches = connected_switches[connected_switches['name'].str.contains('闸刀', na=False)]
            
            # 如果找到连接的闸刀，继续查找连接到的下一个母线（虚拟母线c）
            if not connected_switches.empty and len(connected_switches) == 1:  # 确保只有一把闸刀
                switch = connected_switches.iloc[0]  # 获取唯一的闸刀
                # 确定闸刀连接的另一端母线
                next_bus = switch["element"] if switch["bus"] == remote_bus else switch["bus"]
                if next_bus != remote_bus and net.bus.loc[next_bus, "st_name"] != substation_name:
                    # 找到了虚拟母线c
                    bus_c = next_bus
                    bus_c_name = net.bus.name[bus_c]
                    
                    # 继续查找连接到虚拟母线c的开关（非闸刀）
                    c_connected_switches = net.switch[(net.switch.bus == bus_c) | (net.switch.element == bus_c)]
                    c_connected_switches = c_connected_switches[~c_connected_switches['name'].str.contains('闸刀', na=False)]
                    
                    # 如果找到连接的开关，且只有一个开关，继续查找连接到的下一个母线（虚拟母线d）
                    if not c_connected_switches.empty and len(c_connected_switches) == 1:  # 确保只有一个开关
                        c_switch = c_connected_switches.iloc[0]  # 获取唯一的开关
                        # 确定开关连接的另一端母线
                        bus_d = c_switch["element"] if c_switch["bus"] == bus_c else c_switch["bus"]
                        if bus_d != bus_c and net.bus.loc[bus_d, "st_name"] != substation_name:
                            # 找到了虚拟母线d
                            final_bus_name = net.bus.name[bus_d]
                    else:
                        # 如果没有找到连接到虚拟母线c的开关，或者开关数量不为1，使用虚拟母线c作为最终母线
                        final_bus_name = bus_c_name
            # 获取对侧变电站的供区，添加错误处理以避免list index out of range错误
            try:
                remote_st_name = line.to_st_name if line.to_st_name != substation_name else line.from_st_name
                if "储能" in remote_st_name:
                    print(list(substation_to_island[remote_st_name])[0])
                    equipment["transformers"][remote_st_name] = {
                        "conn_node": final_bus_name,
                        "available": True,
                        "load": [1]*4,  # 使用找到的最终母线作为conn_node
                        "allocate": list(substation_to_island[remote_st_name])[0],
                    }
                else:
                    if remote_st_name in substation_to_island and substation_to_island[remote_st_name]:
                        zone = list(substation_to_island[remote_st_name])[0]
                    else:
                        # 如果对侧变电站没有对应的供区，跳过此线路
                        continue
                    equipment["zone_lines"][line["name"]] = {
                        "available": True,
                        "conn_node": final_bus_name,  # 使用找到的最终母线作为conn_node
                        "zone": zone, #取对侧的
                        "to_st_name": line.to_st_name if line.to_st_name != substation_name else line.from_st_name,
                        "owner": line.owner,
                        "breakers": {},  # 改为字典格式，稍后填充
                        "local_bus_name": local_bus_name,  # 保存本侧母线名称
                        "remote_bus_name": remote_bus_name,  # 保存对侧母线名称
                        "line_id": line["ext_id"]
                    }
            except Exception as e:
                print(f"处理线路 {line['name']} 的供区时出错: {str(e)}")
                continue
            
            # 将合并后的节点添加到设备中
            # if merged_node_name not in equipment["nodes"]:
            #     equipment["nodes"].append(merged_node_name)
            # 修改本侧线路闸刀，连接到合并节点
            for switch_name, switch in equipment["switches"].items():
                if local_bus_name in switch["nodes"]:
                    equipment["switches"][switch_name]["nodes"] = [merged_node_name, switch["nodes"][1] if switch["nodes"][0] == local_bus_name else switch["nodes"][0]]

            # 如果找到了闸刀和虚拟母线c，将它们添加到设备中
            if not connected_switches.empty and len(connected_switches) == 1:
                switch = connected_switches.iloc[0]
                # 添加闸刀到设备中，但连接到合并节点而不是远程母线
                if switch["name"] not in equipment["switches"]:
                    # 确定闸刀的两个端点
                    switch_node1 = net.bus.name[switch["element"]]
                    switch_node2 = net.bus.name[switch["bus"]]
                    equipment["switches"][switch["name"]] = {
                        "available": True,
                        "cost": 5,  # 闸刀成本
                        "initial_state": 1 if switch["closed"] else 0,
                        "nodes": [merged_node_name, switch_node1 if switch_node1 != remote_bus_name else switch_node2],
                        "switch_type": "switch"
                    }
                # 添加虚拟母线c到设备中
                # # 在这里，我们已经确定了bus_c和bus_c_name变量存在，因为它们是在上面的代码中定义的
                # if bus_c_name not in equipment["nodes"]:
                #     equipment["nodes"].append(bus_c_name)
                
                # 如果找到了开关和虚拟母线d，将它们添加到设备中
                # 我们已经确定了c_connected_switches变量存在，因为它是在上面的代码中定义的
                if not c_connected_switches.empty and len(c_connected_switches) == 1:
                    c_switch = c_connected_switches.iloc[0]
                    # 添加开关到设备中
                    if c_switch["name"] not in equipment["switches"]:
                        # 确定开关的两个端点
                        c_switch_node1 = net.bus.name[c_switch["element"]]
                        c_switch_node2 = net.bus.name[c_switch["bus"]]
                        
                        equipment["switches"][c_switch["name"]] = {
                            "available": True,
                            "cost": 1,  # 开关成本
                            "initial_state": 1 if c_switch["closed"] else 0,
                            "nodes": [c_switch_node1, c_switch_node2],
                            "switch_type": "breaker"
                        }
                    
                    # 添加虚拟母线d到设备中
                    # 在这里，我们已经确定了bus_d变量存在，因为它是在上面的代码中定义的
                    # if net.bus.name[bus_d] not in equipment["nodes"]:
                    #     equipment["nodes"].append(net.bus.name[bus_d])
            
        except Exception as e:
            print(f"处理线路 {line['name']} 时出错: {str(e)}")
            pass
    zone_set = set([equipment["zone_lines"][line]["zone"] for line in equipment["zone_lines"]])
    # 收集每条线路相关的所有breaker，分为本侧和对侧
    for line_name, line_info in equipment["zone_lines"].items():
        local_breakers = []  # 本侧breaker（当前变电站内的breaker）
        remote_breakers = []  # 对侧breaker（对侧变电站内的breaker）
        
        conn_node = line_info["conn_node"]
        local_bus_name = line_info["local_bus_name"]
        remote_bus_name = line_info["remote_bus_name"]
        
        for switch_name, switch_info in equipment["switches"].items():
            if switch_info["switch_type"] == "breaker":
                # 检查breaker连接的节点
                switch_nodes = switch_info["nodes"]
                
                # 检查breaker是否与该线路相关（连接到线路的任一节点或remote_bus）
                is_line_related = (conn_node in switch_nodes or 
                                 line_name in switch_nodes or 
                                 local_bus_name in switch_nodes or
                                 remote_bus_name in switch_nodes)
                
                # 额外检查：如果breaker名称包含线路名称（去掉"线"字），也认为是相关的
                line_name_without_suffix = line_name.replace("线", "")
                if line_name_without_suffix in switch_name:
                    is_line_related = True
                
                if is_line_related:
                    # 通过检查switch连接的节点的st_name来判断是本侧还是对侧
                    is_local = False
                    for node_name in switch_nodes:
                        # 查找该节点在net.bus中的信息
                        try:
                            bus_idx = net.bus[net.bus.name == node_name].index[0]
                            node_st_name = net.bus.loc[bus_idx, 'st_name']
                            if node_st_name == substation_name:
                                is_local = True
                                break
                        except (IndexError, KeyError):
                            continue
                    
                    if is_local:
                        local_breakers.append(switch_name)
                    else:
                        remote_breakers.append(switch_name)
        
        equipment["zone_lines"][line_name]["breakers"] = {
            "local": local_breakers[0] if local_breakers else None,
            "remote": remote_breakers[0] if remote_breakers else None
        }
    # drop zone_lines if local or remote break is None
    equipment["zone_lines"] = {k: v for k, v in equipment["zone_lines"].items() if v["breakers"]["local"] and v["breakers"]["remote"]}
        
    for _, transformer in transformers.iterrows():
        equipment["transformers"][transformer["name"]] = {
            "conn_node": net.bus.name[transformer["bus"]],
            "load": [transformer["p_mw"]]*4,
            # "sensitivity": dict(zip(zone_set, [1]*len(zone_set))),
            # "cost": dict(zip(zone_set, [100]*len(zone_set)))
        } 
    equipment["zones"] = list(zone_set)
    # merge nodes in switches
    equipment["nodes"] = list(set([node for switch in equipment["switches"].values() for node in switch["nodes"]]))
    return equipment


def find_islands_without_high_voltage_buses(net, high_voltage_threshold=500):
    # 1. 找出所有电压等级大于等于500kV的母线,并把所有水电机组和燃气机组的出口开关设为 closed
    high_voltage_buses = net.bus[net.bus.vn_kv >= high_voltage_threshold].index.tolist()
    print(
        f"找到{len(high_voltage_buses)}个电压等级大于等于{high_voltage_threshold}kV的母线"
    )
    # 包含水电、燃气的出口开关、所有机组变压器的出口开关
    hydro_gas_node = net.gen.bus[net.gen.type.isin(["水电","天然气"])].tolist() + net.trafo["hv_bus"].tolist()
    # print(hydro_gas_node)
    # 使用loc来避免SettingWithCopyWarning
    net.switch.loc[net.switch.bus.isin(hydro_gas_node) | net.switch.element.isin(hydro_gas_node), "closed"] = True
    # 2. 创建一个不包含高压母线的网络图
    mg = top.create_nxgraph(net, nogobuses=high_voltage_buses)

    # 3. 找出所有的孤岛（连通组件）
    islands = list(top.connected_components(mg))
    print(f"移除高压母线后，网络中有{len(islands)}个孤岛")

    # 4. 统计每个孤岛中包含"变电所"的母线信息
    island_stats = {}
    valid_island_count = 0

    # 创建一个字典，用于存储每个母线所属的孤岛索引
    bus_to_island = {}
    substation_to_island = defaultdict(set)
    for i, island in enumerate(islands):
        for bus in island:
            bus_to_island[int(bus)] = i
            st_name = net.bus.st_name[bus]

    for i, island in enumerate(islands):
        # 获取该孤岛中的所有母线
        island_buses = [int(bus) for bus in island]

        # 找出名称中包含"变"的母线，并保留原始名称和ID
        substation_buses = []
        substation_names = []

        for bus in island_buses:
            if isinstance(net.bus.name[bus], str) and "变电所" in net.bus.name[bus]:
                # 保存母线ID和名称
                substation_buses.append({"id": int(bus), "name": net.bus.name[bus]})
                # 提取变电站名称（用于生成供区名称）
                substation_names.append(net.bus.name[bus])
        # 只保留有变电站母线的孤岛
        if len(substation_buses) > 0:
            valid_island_count += 1

            # 生成供区名称（变电所名称的组合）
            area_name = (
                "+".join(set([name.split("变")[0] for name in substation_names]))
                + "供区"
            )

            # 按照area.json的格式构建数据
            # 查找trafo3w中mv_bus在供区中的列表作为主变
            transformer_list = []
            for idx, trafo3w in net.trafo3w.iterrows():
                if int(trafo3w.mv_bus) in island_buses:
                    # 获取对应母线的名称
                    bus_name = net.bus.name[trafo3w.mv_bus]
                    if isinstance(bus_name, str) and trafo3w.vn_hv_kv == 500:
                        transformer_list.append(
                            {
                                "id": int(idx),
                                "name": str(net.trafo3w.name[idx]).replace("变电所","变"),
                                "capacity": float(trafo3w.sn_mva),
                                "p_current": None
                            }
                        )
            # if len(transformer_list) <= 2:
            #     print(transformer_list)
            operating_generator_dict = {}
            for idx, gen in net.gen[net.gen.type.isin(["煤","天然气"]) & (net.gen.p_mw >= 5)].iterrows():
                if int(gen.bus) in island_buses:
                    operating_generator_dict[str(net.gen.name[idx])] = {
                            "zone": area_name,
                            "id": int(idx),
                            "cost":500,
                            "name": str(net.gen.name[idx]),
                            "p_max": float(gen.max_p_mw),
                            "p_min": float(gen.min_p_mw),
                            "p_current": float(gen.p_mw),
                            "sensitivity": 1,
                            "st_id": net.gen.st_id[idx]
                        }
            backup_generator_dict = {}
            for idx, gen in net.gen[(net.gen.type == "天然气") & (net.gen.p_mw < 5)].iterrows():
                if int(gen.bus) in island_buses:
                    backup_generator_dict[str(net.gen.name[idx])] = {
                            "zone": area_name,
                            "id": int(idx),
                            "available": True,
                            "cost":500,
                            "name": str(net.gen.name[idx]),
                            "p_max": float(gen.max_p_mw),
                            "p_min": max(float(gen.min_p_mw),float(gen.max_p_mw)*0.4),
                            "startup_cost": 10000,
                            "sensitivity": 1,
                            "st_id": net.gen.st_id[idx]
                        }
            hydro_generator_dict = {}
            for idx, gen in net.gen[(net.gen.type == "水电") & (net.gen.p_mw < 5)].iterrows():
                if int(gen.bus) in island_buses:
                    hydro_generator_dict[int(idx)] = {
                            "zone": area_name,
                            "id": int(idx),
                            "available": True,
                            "cost":500,
                            "name": str(net.gen.name[idx]),
                            "p_max": float(gen.max_p_mw),
                            "startup_cost": 1000,
                            "sensitivity": 1,
                            "st_id": net.gen.st_id[idx]
                        }
            storage_units = []
            for idx, storage in net.gen[net.gen.type == "电化学储能"].iterrows():
                if int(storage.bus) in island_buses:
                    storage_units.append(
                        {
                            "zone": area_name,
                            "id": storage.st_id,
                            "name": str(net.gen.name[idx]),
                            "p_charge_max": float(storage.max_p_mw),
                            "p_discharge_max": float(storage.max_p_mw),
                            "soc_initial": 150,
                            "soc_max": 2*float(storage.max_p_mw),
                            "soc_min": 0.2*float(storage.max_p_mw),
                            "p_current": float(storage.p_mw),
                            "st_name": storage.st_name,
                            "st_id": storage.st_id,
                            "sensitivity": 1,
                        }
                    )
            # 按照st_name将储能单元的参数加总
            storage_plant_dict = {}
            for unit in storage_units:
                if unit["st_name"] not in storage_plant_dict:
                    storage_plant_dict[unit["st_name"]] = unit
                    substation_to_island[unit["st_name"]].add(area_name)
                else:
                    storage_plant_dict[unit["st_name"]]["p_charge_max"] += unit["p_charge_max"]
                    storage_plant_dict[unit["st_name"]]["p_discharge_max"] += unit["p_discharge_max"]
                    storage_plant_dict[unit["st_name"]]["soc_max"] += unit["soc_max"]
                    storage_plant_dict[unit["st_name"]]["soc_min"] += unit["soc_min"]
            
            # 保存孤岛信息
            island_stats[area_name] = {
                "主变": transformer_list,  # 使用trafo3w中mv_bus在供区中的列表作为主变列表
                "变电所母线": substation_buses,  # 保留详细信息供参考
                "联络变电站": {},
                "operating_units": operating_generator_dict,
                "backup_units": backup_generator_dict,
                "hydro_units": hydro_generator_dict,
                "storage_units": storage_plant_dict,
                "island_index": i,  # 保存孤岛索引，用于后续识别联络变电站
                "island_buses": island_buses,  # 保存孤岛中的所有母线，用于后续识别联络变电站
            }
    
    for k in island_stats.keys():
        # 获取该孤岛中的所有母线
        for bus in island_stats[k]["island_buses"]:
            if net.bus.type[bus] == "b" and net.bus.vn_kv[bus] == 220:
                st_name = net.bus.st_name[bus]
                substation_to_island[st_name].add(k)
    print(f"有效孤岛数量（包含变电所母线）: {valid_island_count}")
    return island_stats, bus_to_island, islands, substation_to_island


if __name__ == "__main__":
    net = pp.from_sqlite("net.db")
    print("network loaded")
    # 分析网络，找出移除高压母线后的孤岛，并统计每个孤岛中包含"xx变"的母线
    island_stats, bus_to_island, islands, substation_to_island = (
        find_islands_without_high_voltage_buses(net)
    )
    # 识别联络变电站并收集相关设备信息
    interconnection_substation_info = identify_interconnection_substations(
        net, island_stats, bus_to_island, islands, substation_to_island
    )
    #去除各个供区的island_buses
    for k in island_stats.keys():
        del island_stats[k]["island_buses"]
    # 保存结果到JSON文件
    with open("result/area_statistics.json", "w", encoding="utf-8") as f:
        json.dump(island_stats, f, ensure_ascii=False, indent=4)
    print("\n结果已保存到result/area_statistics.json文件")
