import pandapower as pp
import json
import os
import logging
from area_topology import find_islands_without_high_voltage_buses, identify_interconnection_substations

# 配置日志 - 仅在直接运行此文件时配置
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def update_network_for_small_areas(area_name: str = None):
    """
    读取net.db，修改开关状态，然后重新生成island_stats和联络变电站信息
    当供区主变数量≤2时，合上联络变电站中的线路开关和母联开关
    
    参数:
        area_name: 指定要检查的供区名称，如果为None则检查所有供区
    """
    try:
        # 1. 读取net.db
        logging.info("开始读取net.db...")
        net = pp.from_sqlite("net.db")
        logging.info("net.db读取完成")
        
        # 2. 分析网络，找出移除高压母线后的孤岛
        logging.info("开始分析网络...")
        island_stats, bus_to_island, islands, substation_to_island = find_islands_without_high_voltage_buses(net)
        
        # 3. 检查指定供区或所有供区的主变数量
        areas_to_update = []
        if area_name is not None:
            # 只检查指定的供区
            if area_name in island_stats:
                area_data = island_stats[area_name]
                transformers = area_data.get("主变", [])
                if len(transformers) <= 2:
                    logging.info(f"供区 {area_name} 主变数量≤2，需要特殊处理")
                    areas_to_update.append(area_name)
                else:
                    logging.info(f"供区 {area_name} 主变数量>2，无需特殊处理")
            else:
                logging.warning(f"未找到指定的供区: {area_name}")
        else:
            # 检查所有供区
            for area_name, area_data in island_stats.items():
                transformers = area_data.get("主变", [])
                if len(transformers) <= 2:
                    logging.info(f"供区 {area_name} 主变数量≤2，需要特殊处理")
                    areas_to_update.append(area_name)
        
        # 4. 如果有需要更新的供区，修改开关状态
        if areas_to_update:
            # 先识别联络变电站
            interconnection_substation_info = identify_interconnection_substations(
                net, island_stats, bus_to_island, islands, substation_to_island
            )
            
            # 修改开关状态
            for area_name in areas_to_update:
                logging.info(f"开始处理供区 {area_name} 的联络变电站...")
                for substation_name, substation_data in island_stats[area_name].get("联络变电站", {}).items():
                    logging.info(f"处理联络变电站 {substation_name}...")
                    
                    # 收集连接到同一供区的线路
                    zone_lines_by_zone = {}
                    for line_name, line_data in substation_data.get("zone_lines", {}).items():
                        zone = line_data.get("zone")
                        if zone not in zone_lines_by_zone:
                            zone_lines_by_zone[zone] = []
                        zone_lines_by_zone[zone].append({
                            "name": line_name,
                            "conn_node": line_data.get("conn_node")
                        })
                    
                    # 找出连接到同一供区的线路开关
                    for zone, lines in zone_lines_by_zone.items():
                        if len(lines) >= 2 and zone != area_name:
                            logging.info(f"发现连接到供区 {zone} 的多条线路")
                            # 找出连接这些线路的开关
                            for switch_name, switch_data in substation_data.get("switches", {}).items():
                                nodes = switch_data.get("nodes", [])
                                connected_lines = []
                                for node in nodes:
                                    for line in lines:
                                        if line["conn_node"] == node:
                                            connected_lines.append(line["name"])
                                print(connected_lines)
                                # 如果开关连接到同一供区的两条线路，且开关状态为断开，则在net中合上
                                if connected_lines and switch_data.get("initial_state", 0) == 0:
                                    logging.info(f"将连接同一供区的开关 {switch_name} 合上")
                                    # 在net中找到对应的开关并合上
                                    for idx, switch in net.switch.iterrows():
                                        if switch["name"] == switch_name:
                                            net.switch.at[idx, "closed"] = True
                                            break
                                    # 找到并合上开关后立即跳出循环
                                    break
                    # # 合上母联开关
                    # for switch_name, switch_data in substation_data.get("switches", {}).items():
                    #     # 检查是否为母联开关(名称中包含"母联"或者nodes长度为2且不连接到zone_lines)
                    #     is_bus_coupler = "母联" in switch_name
                    #     if is_bus_coupler:
                    #         logging.info(f"将母联开关 {switch_name} 合上")
                    #         # 在net中找到对应的开关并合上
                    #         for idx, switch in net.switch.iterrows():
                    #             if switch["name"] == switch_name:
                    #                 net.switch.at[idx, "closed"] = True
                    #                 break
                    #         # 找到并合上开关后立即跳出循环
                    #         break
            
            # 6. 重新生成island_stats和联络变电站信息
            logging.info("重新生成island_stats和联络变电站信息...")
            island_stats, bus_to_island, islands, substation_to_island = find_islands_without_high_voltage_buses(net)
            interconnection_substation_info = identify_interconnection_substations(
                net, island_stats, bus_to_island, islands, substation_to_island
            )
            
            # 7. 去除各个供区的island_buses
            for k in island_stats.keys():
                if "island_buses" in island_stats[k]:
                    del island_stats[k]["island_buses"]
            
            # 8. 保存结果到JSON文件
            logging.info("保存结果到area_statistics.json文件...")
            with open("result/area_statistics.json", "w", encoding="utf-8") as f:
                json.dump(island_stats, f, ensure_ascii=False, indent=4)
            
            logging.info("处理完成，结果已保存到result/area_statistics.json文件")
            return True
        else:
            logging.info("没有需要特殊处理的供区，跳过更新")
            return False
    except Exception as e:
        logging.error(f"更新网络时发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # 直接运行脚本时检查所有供区
    update_network_for_small_areas("夏金供区")