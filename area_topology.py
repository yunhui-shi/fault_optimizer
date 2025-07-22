import pandapower as pp
import pandapower.topology as top
import pandas as pd
import networkx as nx
import json
from collections import defaultdict

def identify_interconnection_substations(net, island_stats, bus_to_island, islands):
    """
    识别联络变电站并收集相关设备信息
    联络变电站定义：供区内的变电站中有一条线与别的供区相连
    """
    print("\n开始识别联络变电站...")
    
    # 创建一个字典，用于存储每个变电站的设备信息
    interconnection_substation_info = {}
    
    # 创建区域名称到区域索引的映射
    area_to_island_index = {area_name: info["island_index"] for area_name, info in island_stats.items()}
    
    # 遍历所有线路，检查是否连接不同的供区
    for line_idx, line in net.line.iterrows():
        from_bus = int(line.from_bus)
        to_bus = int(line.to_bus)
        
        # 检查两个母线是否存在于bus_to_island中
        if from_bus in bus_to_island and to_bus in bus_to_island:
            # 检查两个母线是否属于不同的孤岛
            if bus_to_island[from_bus] != bus_to_island[to_bus]:
                # 找出这两个母线所属的变电站
                from_bus_name = net.bus.name[from_bus] if isinstance(net.bus.name[from_bus], str) else ""
                to_bus_name = net.bus.name[to_bus] if isinstance(net.bus.name[to_bus], str) else ""
                
                # 检查母线是否属于变电站
                for bus, bus_name in [(from_bus, from_bus_name), (to_bus, to_bus_name)]:
                    if "变" in bus_name:
                        # 提取变电站名称
                        if "变电所" in bus_name:
                            substation_name = bus_name.split("变电所")[0] + "变电所"
                        else:
                            substation_name = bus_name.split("变")[0] + "变"
                        
                        # 找出该母线所属的供区
                        island_idx = bus_to_island[bus]
                        area_name = None
                        for name, info in island_stats.items():
                            if info["island_index"] == island_idx:
                                area_name = name
                                break
                        
                        if area_name:
                            # 如果该变电站尚未被识别为联络变电站，则添加到列表中
                            if substation_name not in interconnection_substation_info:
                                # 收集变电站内的设备信息
                                substation_equipment = collect_substation_equipment(net, substation_name, islands[island_idx])
                                
                                # 添加到联络变电站信息字典
                                interconnection_substation_info[substation_name] = {
                                    "area": area_name,
                                    "equipment": substation_equipment
                                }
                                
                                # 添加到对应供区的联络变电站列表
                                if area_name in island_stats:
                                    island_stats[area_name]["联络变电站"].append({
                                        "name": substation_name,
                                        "id": int(bus)
                                    })
    
    print(f"识别出{len(interconnection_substation_info)}个联络变电站")
    return interconnection_substation_info

def collect_substation_equipment(net, substation_name, island_buses):
    """
    收集变电站内的母线、开关、变压器、线路信息
    """
    equipment = {
        "buses": [],      # 母线
        "switches": [],   # 开关
        "transformers": [], # 变压器
        "lines": []      # 线路
    }
    
    # 收集变电站内的母线
    substation_buses = []
    for bus_idx in island_buses:
        bus_idx = int(bus_idx)  # 确保是Python原生int类型
        bus_name = net.bus.name[bus_idx] if isinstance(net.bus.name[bus_idx], str) else ""
        # 检查是否包含变电站名称
        if substation_name in bus_name:
            substation_buses.append(bus_idx)
            equipment["buses"].append({
                "id": int(bus_idx),
                "name": str(bus_name),
                "vn_kv": float(net.bus.vn_kv[bus_idx])
            })
    
    # 收集变电站内的开关
    for switch_idx, switch in net.switch.iterrows():
        # 检查开关是否连接到变电站内的母线
        if int(switch.bus) in substation_buses or (int(switch.element) in substation_buses and switch.et == "b"):
            switch_name = net.switch.name[switch_idx] if isinstance(net.switch.name[switch_idx], str) else f"switch_{switch_idx}"
            equipment["switches"].append({
                "id": int(switch_idx),
                "name": str(switch_name),
                "closed": bool(switch.closed),
                "bus": int(switch.bus),
                "element": int(switch.element)
            })
    
    # 收集变电站内的变压器（两绕组）
    for trafo_idx, trafo in net.trafo.iterrows():
        if int(trafo.hv_bus) in substation_buses or int(trafo.lv_bus) in substation_buses:
            trafo_name = net.trafo.name[trafo_idx] if isinstance(net.trafo.name[trafo_idx], str) else f"trafo_{trafo_idx}"
            equipment["transformers"].append({
                "id": int(trafo_idx),
                "name": str(trafo_name),
                "type": "trafo",
                "hv_bus": int(trafo.hv_bus),
                "lv_bus": int(trafo.lv_bus),
                "sn_mva": float(trafo.sn_mva)
            })
    
    # 收集变电站内的变压器（三绕组）
    for trafo3w_idx, trafo3w in net.trafo3w.iterrows():
        if int(trafo3w.hv_bus) in substation_buses or int(trafo3w.mv_bus) in substation_buses or int(trafo3w.lv_bus) in substation_buses:
            trafo3w_name = net.trafo3w.name[trafo3w_idx] if isinstance(net.trafo3w.name[trafo3w_idx], str) else f"trafo3w_{trafo3w_idx}"
            equipment["transformers"].append({
                "id": int(trafo3w_idx),
                "name": str(trafo3w_name),
                "type": "trafo3w",
                "hv_bus": int(trafo3w.hv_bus),
                "mv_bus": int(trafo3w.mv_bus),
                "lv_bus": int(trafo3w.lv_bus),
                "sn_hv_mva": float(trafo3w.sn_hv_mva)
            })
    
    # 收集变电站内的线路
    for line_idx, line in net.line.iterrows():
        if int(line.from_bus) in substation_buses or int(line.to_bus) in substation_buses:
            line_name = net.line.name[line_idx] if isinstance(net.line.name[line_idx], str) else f"line_{line_idx}"
            equipment["lines"].append({
                "id": int(line_idx),
                "name": str(line_name),
                "from_bus": int(line.from_bus),
                "to_bus": int(line.to_bus),
                "length_km": float(line.length_km),
                "max_i_ka": float(line.max_i_ka)
            })
    
    return equipment

def find_islands_without_high_voltage_buses(net, high_voltage_threshold=500):
    # 1. 找出所有电压等级大于等于500kV的母线
    high_voltage_buses = net.bus[net.bus.vn_kv >= high_voltage_threshold].index.tolist()
    print(f"找到{len(high_voltage_buses)}个电压等级大于等于{high_voltage_threshold}kV的母线")
    
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
    for i, island in enumerate(islands):
        for bus in island:
            bus_to_island[int(bus)] = i
    
    for i, island in enumerate(islands):
        # 获取该孤岛中的所有母线
        island_buses = [int(bus) for bus in island]
        
        # 找出名称中包含"变"的母线，并保留原始名称和ID
        substation_buses = []
        substation_names = []
        
        for bus in island_buses:
            if isinstance(net.bus.name[bus], str) and "变" in net.bus.name[bus]:
                # 保存母线ID和名称
                substation_buses.append({
                    "id": int(bus),
                    "name": net.bus.name[bus]
                })
                # 提取变电站名称（用于生成供区名称）
                substation_names.append(net.bus.name[bus])      
        # 只保留有变电站母线的孤岛
        if len(substation_buses) > 0:
            valid_island_count += 1
            
            # 生成供区名称（变电所名称的组合）
            area_name = "+".join(set([name.split("变")[0] for name in substation_names])) + "供区"
            
            # 按照area.json的格式构建数据
            # 查找trafo3w中mv_bus在供区中的列表作为主变
            transformer_list = []
            for idx, trafo3w in net.trafo3w.iterrows():
                if int(trafo3w.mv_bus) in island_buses:
                    # 获取对应母线的名称
                    bus_name = net.bus.name[trafo3w.mv_bus]
                    if isinstance(bus_name, str):
                        transformer_list.append({
                            "id": int(idx),
                            "name": str(net.trafo3w.name[idx]),
                            "capacity": float(trafo3w.sn_mva)
                        })
            # 初始化联络变电站列表
            interconnection_substations = []
            
            # 保存孤岛信息
            island_stats[area_name] = {
                "主变": transformer_list,  # 使用trafo3w中mv_bus在供区中的列表作为主变列表
                "变电所母线": substation_buses,  # 保留详细信息供参考
                "联络变电站": interconnection_substations,
                "island_index": i,  # 保存孤岛索引，用于后续识别联络变电站
                "island_buses": island_buses  # 保存孤岛中的所有母线，用于后续识别联络变电站
            }
    
    print(f"有效孤岛数量（包含变电所母线）: {valid_island_count}")
    return island_stats, bus_to_island, islands

if __name__ == "__main__":
    net = pp.from_sqlite("net.db")
    print("network loaded")
    
    # 分析网络，找出移除高压母线后的孤岛，并统计每个孤岛中包含"xx变"的母线
    island_stats, bus_to_island, islands = find_islands_without_high_voltage_buses(net)
    
    # 识别联络变电站并收集相关设备信息
    interconnection_substation_info = identify_interconnection_substations(net, island_stats, bus_to_island, islands)
    
    # 打印结果
    print("\n孤岛统计结果:")
    print(json.dumps(island_stats, ensure_ascii=False, indent=4))
    
    # 保存结果到JSON文件
    with open("area_statistics.json", "w", encoding="utf-8") as f:
        json.dump(island_stats, f, ensure_ascii=False, indent=4)
    print("\n结果已保存到area_statistics.json文件")
    
    # 保存联络变电站信息到JSON文件
    with open("interconnection_substations.json", "w", encoding="utf-8") as f:
        json.dump(interconnection_substation_info, f, ensure_ascii=False, indent=4)
    print("\n联络变电站信息已保存到interconnection_substations.json文件")
    