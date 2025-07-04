import json
import networkx as nx
from collections import defaultdict

def load_power_system_to_graph(json_data):
    # Create a directed graph
    G = nx.Graph()
    
    # Add all substation nodes to the graph
    for node in json_data["substation_nodes"]:
        G.add_node(node, type="substation_node")
    
    # Add all switches as edges, but only include those with initial_state=1
    active_edges = []
    for switch_name, switch_data in json_data["switches"].items():
        node1, node2 = switch_data["nodes"]
        edge_data = {
            "type": "switch",
            "switch_name": switch_name,
            "switch_type": switch_data["switch_type"],
            "cost": switch_data["cost"],
            "initial_state": switch_data["initial_state"],
            "available": switch_data["available"]
        }
        G.add_edge(node1, node2, **edge_data)
        if switch_data["initial_state"] == 1:
            active_edges.append((node1, node2))
    
    # Create a subgraph with only active edges (initial_state=1)
    active_G = nx.Graph()
    active_G.add_nodes_from(G.nodes(data=True))
    active_G.add_edges_from([(u, v, G.edges[u, v]) for u, v in active_edges])
    
    # Find connected components in the active subgraph
    connected_components = list(nx.connected_components(active_G))
    
    # Add connected component ID to each node
    for component_id, component in enumerate(connected_components):
        for node in component:
            G.nodes[node]["connected_component"] = component_id
    
    # Add connected component info to edges
    for u, v, data in G.edges(data=True):
        u_comp = G.nodes[u].get("connected_component", -1)
        v_comp = G.nodes[v].get("connected_component", -1)
        data["connected_components"] = (u_comp, v_comp)
    
    # Create dictionaries to store which objects are connected to which nodes and zones
    node_objects = defaultdict(list)
    zone_objects = defaultdict(list)
    
    # Helper function to process objects
    def process_objects(obj_type, objects_dict, conn_node_key="conn_node", zone_key="zone"):
        for name, data in objects_dict.items():
            if conn_node_key in data:
                conn_node = data[conn_node_key]
                node_objects[conn_node].append((obj_type, name))
                # Add connected component info to the object's node
                if conn_node in G:
                    component_id = G.nodes[conn_node].get("connected_component", -1)
                    node_objects[conn_node][-1] = node_objects[conn_node][-1] + (component_id,)
            if zone_key in data:
                zone_objects[data[zone_key]].append((obj_type, name))
    
    # Process all object types
    process_objects("transformer", json_data["transformers"])
    process_objects("zone_line", json_data["zone_lines"])
    process_objects("backup_unit", json_data["backup_units"], conn_node_key=None)
    process_objects("hydro_unit", json_data["hydro_units"], conn_node_key=None)
    process_objects("operating_unit", json_data["operating_units"], conn_node_key=None)
    process_objects("storage_unit", json_data["storage_units"], conn_node_key=None)
    process_objects("interruptible_load", json_data["interruptible_loads"], conn_node_key=None)
    
    # Add node objects and zone objects as graph attributes
    G.graph["node_objects"] = dict(node_objects)
    G.graph["zone_objects"] = dict(zone_objects)
    G.graph["zones"] = json_data["zones"]
    G.graph["connected_components"] = connected_components
    
    return G

def print_graph_summary(G):
    print(f"Graph loaded with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")
    print(f"Found {len(G.graph['connected_components'])} connected components")
    
    print("\nConnected components:")
    for i, component in enumerate(G.graph["connected_components"]):
        print(f"Component {i}: {len(component)} nodes")
        print(f"  Sample nodes: {list(component)}")
    
    print("\nSample node attributes:")
    for node in list(G.nodes()):
        print(f"{node}: {G.nodes[node]}")
    
    print("\nSample edge attributes:")
    for edge in list(G.edges(data=True)):
        print(f"{edge[0]} -- {edge[1]}: {edge[2]}")

def build_power_system_graph(substation_nodes: list, switches: dict,):
    """
    构建电力系统图结构并分析连通子图
    :param substation_nodes: 节点
    :param switches: 边
    :return: 带连通子图属性的NetworkX图对象
    """
    # 创建无向图
    G = nx.Graph()
    
    # 1. 添加所有变电站节点
    for node in substation_nodes:
        G.add_node(node)
    
    # 2. 添加所有开关设备作为边
    for switch_name, switch_data in switches.items():
        node1, node2 = switch_data["nodes"]
        edge_data = {
            "type": "switch",
            "switch_name": switch_name,
            "switch_type": switch_data["switch_type"],
            "cost": switch_data["cost"],
            "initial_state": switch_data["initial_state"],
            "available": switch_data["available"]
        }
        G.add_edge(node1, node2, **edge_data)
    
    # 3. 创建仅包含初始连通开关的子图
    active_edges = [(u, v) for u, v, d in G.edges(data=True) 
                   if d["initial_state"] == 1]
    active_G = nx.Graph()
    active_G.add_nodes_from(G.nodes(data=True))
    active_G.add_edges_from([(u, v, G.edges[u, v]) for u, v in active_edges])
    
    # 4. 查找连通子图
    connected_components = list(nx.connected_components(active_G))
    
    # 5. 为节点添加连通子图属性
    for component_id, component in enumerate(connected_components):
        for node in component:
            G.nodes[node]["connected_component"] = component_id
    
    # 6. 为边添加连通子图端点信息
    for u, v, data in G.edges(data=True):
        u_comp = G.nodes[u].get("connected_component", -1)  # -1表示不连通
        v_comp = G.nodes[v].get("connected_component", -1)
        data["connected_components"] = (u_comp, v_comp)
    
    # 7. 添加元数据
    G.graph["connected_components"] = connected_components
    G.graph["component_count"] = len(connected_components)
    
    return G

def get_connected_edges_with_attrs(G, u, v):
    """获取与边(u,v)相连的其他边（带属性）"""
    connected_edges = []
    
    # 获取u节点的所有邻居边
    if (not "bus" in u) and (not "母线" in u) and (not "正母" in u) and (not "副母" in u):
        for neighbor in G.neighbors(u):
            if neighbor != v:  # 排除当前边
                edge_data = G.get_edge_data(u, neighbor)
                connected_edges.append((u, neighbor, edge_data))
    
    # 获取v节点的所有邻居边
    if (not "bus" in v) and (not "母线" in v) and (not "正母" in v) and (not "副母" in v):
        for neighbor in G.neighbors(v):
            if neighbor != u:  # 排除当前边
                edge_data = G.get_edge_data(v, neighbor)
                connected_edges.append((v, neighbor, edge_data))
    
    return connected_edges

def analyze_components(G):
    """分析并打印连通子图信息"""
    print(f"系统共包含 {G.number_of_nodes()} 个节点和 {G.number_of_edges()} 条边")
    print(f"发现 {G.graph['component_count']} 个连通子图\n")
    
    # 打印每个连通子图信息
    for i, component in enumerate(G.graph["connected_components"]):
        print(f"连通子图 #{i}:")
        print(f"  包含节点数: {len(component)}")
        
        # 统计不同类型节点
        type_count = defaultdict(int)
        for node in component:
            if "type" in G.nodes[node]:
                type_count[G.nodes[node]["type"]] += 1
        print(f"  节点类型分布: {dict(type_count)}")
        
        # 打印部分节点示例
        sample_nodes = list(component)[:3]
        print(f"  示例节点: {sample_nodes}{'...' if len(component)>3 else ''}\n")

    print("\nSample edge attributes:")
    for edge in list(G.edges(data=True)):
        print(f"{edge[0]} -- {edge[1]}: {edge[2]}")

# Example usage:
if __name__ == "__main__":
    # Load the JSON data (in this case, we'll use the provided dictionary)
    with open("power_system_test.json", "r", encoding='utf-8') as f:
        json_data = json.load(f)
    
    # Alternatively, if you have the data as a Python dictionary:
    # json_data = { ... } # (the entire JSON data from the question)
    
    # Create the graph from json data
    # power_system_graph = load_power_system_to_graph(json_data)
    # print_graph_summary(power_system_graph)

    # 构建图结构
    power_graph = build_power_system_graph(json_data["substation_nodes"], json_data["switches"])
    # 分析连通子图
    analyze_components(power_graph)
    