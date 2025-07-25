import pandapower as pp
import pandapower.topology as top
import time, json, os
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

# TODO: 需要本地做映射库, 映射模型ID与真实计算ID
# TODO: 所有必传参数都不能为空值
# TODO: 开关未能正确加载
# TODO: 电网可以本地生成sqlite库, 调整后再加载

bus_map = {}
line_map = {}
trafo_map = {}

def load_file(file_name):
    file_path = f"./断面数据/{file_name}.json"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="gbk") as file:
            return json.load(file)
    else:
        return []


def load_measurement_data(api_endpoint):
    return []


def load_bus_section(net, pf_data):
    print("Starting Bus Section")
    bus_data = pf_data["BUSBAR"]
    for bus in bus_data:
        index = pp.create_bus(
            net,
            name=bus["name"],
            vn_kv=float(bus["vn_kv"]),
            max_vm_pu=float(bus["max_vm_pu"]),
            min_vm_pu=float(bus["min_vm_pu"]),
            st_name = bus["st_name"],
            type=bus["type"],
        )
        bus_map[int(bus["id"])] = int(index)
        #print(f"{bus} created")

    # 加载量测数据
    bus_measurements = load_measurement_data("api/measurement/bus")
    print("Bus measurements loaded.")
    print("Bus Section completed\n")

def load_trafo_section(net, pf_data):
    print("Starting Transformer Section")
    trafo2_data = pf_data["TRANSFORMER"]
    for tranfo in trafo2_data:
        index = pp.create_transformer_from_parameters(
            net,
            index=int(tranfo["id"]),
            name=tranfo["name"],
            hv_bus=bus_map[int(tranfo["hv_bus"])],
            lv_bus=bus_map[int(tranfo["lv_bus"])],
            sn_mva=float(tranfo["sn_mva"]),
            vn_hv_kv=float(tranfo["vn_hv_kv"]),
            vn_lv_kv=float(tranfo["vn_lv_kv"]),
            vkr_percent=float(tranfo["vkr_percent"]),
            vk_percent=float(tranfo["vk_percent"]),
            pfe_kw=float(tranfo["pfe_kw"]),
            i0_percent=float(tranfo["i0_percent"]),
        )
        trafo_map[int(tranfo["id"])] = int(index)
    trafo3_data = pf_data["TRANSFORMER3"]
    for tranfo in trafo3_data:
        index = pp.create_transformer3w_from_parameters(
            net,
            index=int(tranfo["id"]),
            name=tranfo["name"],
            hv_bus=bus_map[int(tranfo["hv_bus"])],
            mv_bus=bus_map[int(tranfo["mv_bus"])],
            lv_bus=bus_map[int(tranfo["lv_bus"])],
            sn_mva=float(tranfo["sn_mva"]),
            sn_hv_mva=float(tranfo["sn_hv_mva"]),
            sn_mv_mva=float(tranfo["sn_mv_mva"]),
            sn_lv_mva=float(tranfo["sn_lv_mva"]),
            vn_hv_kv=float(tranfo["vn_hv_kv"]),
            vn_mv_kv=float(tranfo["vn_mv_kv"]),
            vn_lv_kv=float(tranfo["vn_lv_kv"]),
            vkr_percent=float(tranfo["vkr_percent"]),
            vkr_hv_percent=float(tranfo["vkr_percent"]),
            vkr_mv_percent=float(tranfo["vkr_percent"]),
            vkr_lv_percent=float(tranfo["vkr_percent"]),
            vk_percent=float(tranfo["vk_percent"]),
            vk_hv_percent=float(tranfo["vk_percent"]),
            vk_mv_percent=float(tranfo["vk_percent"]),
            vk_lv_percent=float(tranfo["vk_percent"]),
            pfe_kw=float(tranfo["pfe_kw"]),
            i0_percent=float(tranfo["i0_percent"]),
        )
        trafo_map[int(tranfo["id"])] = int(index)
    # 加载量测数据
    trafo_measurements = load_measurement_data("api/measurement/trafo")
    print("Transformer measurements loaded.")
    print("Transformer Section completed\n")


def load_line_section(net, pf_data):
    print("Starting Line Section")
    line_data = pf_data["ACLINE"]
    for line in line_data:
        try:
            index = pp.create_line_from_parameters(
                net,
                # index=int(line["ID"]),
                name=line["name"],
                from_bus=bus_map[int(line["from_bus"])],
                to_bus=bus_map[int(line["to_bus"])],
                length_km=float(line["length_km"]),
                r_ohm_per_km=float(line["r_ohm_per_km"]),
                x_ohm_per_km=float(line["x_ohm_per_km"]),
                # c_nf_per_km=float(line["C_NF_PER_KM"]),
                c_nf_per_km=0.00117,
                max_i_ka=float(line["max_i_ka"]) if float(line["max_i_ka"]) else 3 ,
                from_st_name=line["from_st_name"],
                to_st_name=line["to_st_name"],
            )
            line_map[int(line["id"])] = int(index)
        except Exception as e:
            print(line, e)
    # 加载量测数据
    line_measurements = load_measurement_data("api/measurement/line")
    print("Line measurements loaded.")
    print("Line Section completed\n")


def load_gen_section(net, pf_data):
    print("Starting Generator Section")
    gen_data = pf_data["GEN"]
    for gen in gen_data:
        try:
            pp.create_gen(
                net,
                index=int(gen["id"]),
                st_id=gen["st_id"],
                name=gen["name"],
                bus=bus_map[int(gen["bus"])],
                p_mw=max(float(gen["p_mw"]), 0),
                sn_mva=1000,
                vm_pu=1.02,
                max_p_mw=float(gen["p_max"]),
                min_p_mw=float(gen["p_min"]),
                type=gen["gen_type"],
                st_name=gen["st_name"],
            )
        except Exception as e:
            print(gen, e)
    # 加载量测数据
    gen_measurements = load_measurement_data("api/measurement/gen")
    print("Generator measurements loaded.")
    print("Generator Section completed\n")


def load_load_section(net, pf_data):
    print("Starting Load and Static Generator Section")
    load_data = pf_data["LOAD"]
    for load in load_data:
        try:
            # 添加负荷
            pp.create_load(
                net,
                index=load["id"],
                name=load["name"],
                bus=bus_map[int(load["bus"])],
                p_mw=float(load["p_mw"]),
                q_mvar=float(load["q_mvar"]),
            )
        except Exception as e:
            print(load, e)
    # 加载量测数据
    load_measurements = load_measurement_data("api/measurement/load")
    print("Load measurements loaded.")
    print("Load Section completed\n")

def load_static_gen_section(net, pf_data):
    print("Starting Static Generator Section")
    gen_data = pf_data["STATISTIC_GEN"]
    # print('static gen-data', gen_data)
    for gen in gen_data:
        try:
            #print(gen["BUS"], gen["NAME"], "static-gen-bus")
            pp.create_sgen(
                net,
                index=int(gen["id"]),
                name=gen["name"],
                bus=bus_map[int(gen["bus"])],
                p_mw=-float(gen["p_mw"]),
                q_mvar=-float(gen["q_mvar"]),
            )
        except Exception as e:
            print(gen, e)
    # 加载量测数据
    gen_measurements = load_measurement_data("api/measurement/static-gen")
    print("Static generator measurements loaded.")
    print("Static generator Section completed\n")

def load_switch_section(net, pf_data):
    print("Starting Switch Section")
    for switch in pf_data["KG"]:
        if switch["et"] == "b":
            element = bus_map[int(switch["element"])]
        elif switch["et"] == "l":
            element = line_map[int(switch["element"])]
        elif switch["et"] == "t" or switch['et'] == 't3':
            element = trafo_map[int(switch["element"])]
        try:
            pp.create_switch(
                net,
                # index=switch["ID"],
                name=switch["name"],
                bus=bus_map[int(switch["bus"])],
                et=switch["et"],
                element=element,
                closed=True if switch["closed"]=="1" else False
            )
        except Exception as e:
            print(switch, e)
    # 加载量测数据
    switch_measurements = load_measurement_data("api/measurement/switch")
    print("Switch measurements loaded.")
    print("Switch Section completed\n")

# 潮流计算
def run_powerflow():
    net = pp.from_sqlite("net.db")
    print("network loaded")
    print("总负荷有功：", net.load.p_mw.sum())
    print("总机组有功：", net.gen.p_mw.sum()+net.sgen.p_mw.sum())
    mg = top.create_nxgraph(net)  
    cc = list(top.connected_components(mg))
    print(f"网络有 {len(cc)} 个连通分量")
    print("Starting Power Flow Calculation")
    pp.create_continuous_elements_index(net) 
    # 获取诊断结果字典  
    # diag_results = pp.diagnostic(net, report_style='None')  # 不输出到控制台  
    # # 保存到JSON文件  
    # with open('diagnostic_results.json', 'w') as f:  
    #     json.dump(diag_results, f, indent=2)

    # # 检查并删除无效引用
    # elements_with_bus = [
    #     "line",
    #     "trafo",
    #     "switch",
    #     "load",
    #     "gen",
    #     "sgen",
    #     "ward",
    #     "xward",
    # ]

    # for element in elements_with_bus:
    #     if element in net and not net[element].empty:
    #         if element in ["line", "trafo", "switch"]:
    #             from_bus_col = (
    #                 "from_bus"
    #                 if element == "switch"
    #                 else "hv_bus" if element == "trafo" else "from_bus"
    #             )
    #             to_bus_col = (
    #                 "to_bus"
    #                 if element == "switch"
    #                 else "lv_bus" if element == "trafo" else "to_bus"
    #             )
    #             bus_columns = [from_bus_col, to_bus_col]
    #         else:
    #             bus_columns = ["bus"]

    #         for bus_col in bus_columns:
    #             invalid_ids = net[element][
    #                 ~net[element][bus_col].isin(net.bus.index)
    #             ].index
    #             if len(invalid_ids) > 0:
    #                 print(
    #                     f"Deleting {len(invalid_ids)} invalid entries in {element} (invalid {bus_col})"
    #                 )
    #                 net[element].drop(invalid_ids, inplace=True)

    # # 先重置母线索引
    # pp.create_continuous_bus_index(net)
    # # 重置所有元件索引, 避免内存溢出
    # pp.toolbox.create_continuous_elements_index(net)
    import time
    t = time.time()
    pp.runpp(net, max_iteration= 30, tolerance_mva=1e-6)
    print(time.time() - t)
    pp.to_excel(net, "network_with_results.xlsx", include_results=True)
    # print(net.res_bus.loc[net.gen.bus])
    print("Power Flow Completed")
    high_vol_bus = pd.merge(
        net.res_bus.nlargest(5, 'vm_pu'),
        net.bus,
        left_index=True,
        right_index=True
    )
    low_vol_bus = pd.merge(
        net.res_bus.nsmallest(5, 'vm_pu'),
        net.bus,
        left_index=True,
        right_index=True
    )
    result = {
        "电压最高母线": high_vol_bus.to_json(),
        "电压最低母线": low_vol_bus.to_json(),
    }
    return high_vol_bus

# 创建网络模型并存库
def create_network(pf_data):
    # 创建空网络
    net = pp.create_empty_network(name="浙江电网", sn_mva=1000)
    try:
        os.system("del net.db")
    except:
        pass
    # 环节1：加载母线
    load_bus_section(net, pf_data)

    # 环节2-5：并行执行
    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(load_trafo_section, net, pf_data),
            executor.submit(load_line_section, net, pf_data),
            executor.submit(load_gen_section, net, pf_data),
            executor.submit(load_load_section, net, pf_data),
            executor.submit(load_static_gen_section, net, pf_data),
        ]

        for future in futures:
            future.result()

    # 环节6：加载开关
    load_switch_section(net, pf_data)

    # 设置外部电网节点
    pp.create_ext_grid(net, bus=54, vm_pu=1.02)
    # pp.create_ext_grid(net, bus=50, vm_pu=1.02)

    # 保存原始数据
    pp.to_sqlite(net, "./net.db")
    return net


if __name__ == "__main__":
    # pf_data = load_file("pfdata")
    # 尝试调用API接口获取优化边界
    # import requests
    # from datetime import datetime
    # from dotenv import load_dotenv
    # load_dotenv(".env.example")
    # try:
    #     api_url = os.getenv("POWER_FLOW_URL")
    #     current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
    #     dataTime = current_hour.strftime("%Y-%m-%d %H:%M:%S")
    #     params = {
    #         "dataTime": dataTime
    #     }
    #     response = requests.post(api_url, params=params, timeout=180)
    #     # 检查响应状态码是否为2xx
    #     if response.status_code // 100 == 2:
    #         # API调用成功，处理返回的数据
    #         pf_data = response.json()
    #         create_network(pf_data)
    #         result = run_powerflow()
    #         print(result)
    # except requests.exceptions.RequestException as e:
    #     # 网络错误或超时，使用默认数据
    #     print(f"API调用异常（{str(e)}）")
    # except Exception as e:
    #     print(f"API调用异常（{str(e)}）")    
    result = run_powerflow()
    print(result)
    