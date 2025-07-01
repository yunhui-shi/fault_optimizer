import pandapower as pp
import time, json, os
from concurrent.futures import ThreadPoolExecutor

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
        with open(file_path, "r", encoding="utf-8") as file:
            return json.load(file)
    else:
        return []


def load_element_data(api_endpoint):
    if api_endpoint == "api/bus":
        return load_file("母线")
    if api_endpoint == "api/绕组":
        return load_file("绕组")
    if api_endpoint == "api/线端":
        return load_file("线端")
    elif api_endpoint == "api/trafo_2":
        return load_file("变压器-双")
    elif api_endpoint == "api/trafo_3":
        return load_file("变压器-三")
    elif api_endpoint == "api/line":
        return load_file("交流线路")
    elif api_endpoint == "api/gen":
        return load_file("机组")
    elif api_endpoint == "api/static-gen":
        return load_file("静态机组")
    elif api_endpoint == "api/load":
        return load_file("负荷")
    elif api_endpoint == "api/switch":
        return load_file("开关")
    elif api_endpoint == "api/switch_i":
        return load_file("母线-线路开关")
    elif api_endpoint == "api/switch_t":
        return load_file("母线-变压器开关")
    elif api_endpoint == "api/switch_b":
        return load_file("母线-母线开关") + load_file("母线-母线开关 500+")
    else:
        return []


def load_measurement_data(api_endpoint):
    return []


def load_bus_section(net):
    print("Starting Bus Section")
    bus_data = load_element_data("api/bus")
    for bus in bus_data:
        index = pp.create_bus(
            net,
            name=bus["NAME"],
            vn_kv=float(bus["VN_KV"]),
            max_vm_pu=float(bus["MAX_VM_PU"]),
            min_vm_pu=float(bus["MIN_VM_PU"]),
        )
        bus_map[int(bus["INDEX"])] = int(index)
    for bus in load_element_data("api/绕组"):
        index = pp.create_bus(
            net,
            name=bus["NAME"],
            vn_kv=float(bus["电压等级"]),
            max_vm_pu=None,
            min_vm_pu=None,
        )
        bus_map[int(bus["ID"])] = index
    for bus in load_element_data("api/线端"):
        index = pp.create_bus(
            net,
            name=bus["NAME"],
            vn_kv=float(bus["电压等级"]),
            max_vm_pu=None,
            min_vm_pu=None,
        )
        bus_map[int(bus["ID"])] = index

    # 加载量测数据
    bus_measurements = load_measurement_data("api/measurement/bus")
    print("Bus measurements loaded.")
    print("Bus Section completed\n")


def load_trafo_section(net):
    print("Starting Transformer Section")
    trafo2_data = load_element_data("api/trafo_2")
    for tranfo in trafo2_data:
        index = pp.create_transformer_from_parameters(
            net,
            # index=int(tranfo["ID"]),
            name=tranfo["NAME"],
            hv_bus=bus_map[int(tranfo["HV_BUS"])],
            lv_bus=bus_map[int(tranfo["LV_BUS"])],
            sn_mva=999,
            vn_hv_kv=999,
            vn_lv_kv=999,
            vkr_percent=999,
            vk_percent=999,
            pfe_kw=999,
            i0_percent=999,
        )
        trafo_map[int(tranfo["ID"])] = int(index)
    trafo3_data = load_element_data("api/trafo_3")
    for tranfo in trafo3_data:
        index = pp.create_transformer3w_from_parameters(
            net,
            # index=int(tranfo["ID"]),
            name=tranfo["NAME"],
            hv_bus=bus_map[int(tranfo["HV_BUS"])],
            mv_bus=bus_map[int(tranfo["MV_BUS"])],
            lv_bus=bus_map[int(tranfo["LV_BUS"])],
            sn_mva=int(tranfo["SN_MVA"]),
            sn_hv_mva=int(tranfo["SN_MVA"]),
            sn_mv_mva=int(tranfo["SN_MVA"]),
            sn_lv_mva=int(tranfo["SN_MVA"]),
            vn_hv_kv=int(tranfo["VN_HV_KV"]),
            vn_mv_kv=int(tranfo["VN_MV_KV"]),
            vn_lv_kv=int(tranfo["VN_LV_KV"]),
            vkr_percent=float(tranfo["VKR_PERCENT"]),
            vkr_hv_percent=float(tranfo["VKR_PERCENT"]),
            vkr_mv_percent=float(tranfo["VKR_PERCENT"]),
            vkr_lv_percent=float(tranfo["VKR_PERCENT"]),
            vk_percent=float(tranfo["VK_PERCENT"]),
            vk_hv_percent=float(tranfo["VK_PERCENT"]),
            vk_mv_percent=float(tranfo["VK_PERCENT"]),
            vk_lv_percent=float(tranfo["VK_PERCENT"]),
            pfe_kw=int(tranfo["PFE_KW"]),
            i0_percent=float(tranfo["I0_PERCENT"]),
        )
        trafo_map[int(tranfo["ID"])] = int(index)
    # 加载量测数据
    trafo_measurements = load_measurement_data("api/measurement/trafo")
    print("Transformer measurements loaded.")
    print("Transformer Section completed\n")


def load_line_section(net):
    print("Starting Line Section")
    line_data = load_element_data("api/line")
    for line in line_data:
        try:
            index = pp.create_line_from_parameters(
                net,
                # index=int(line["ID"]),
                name=line["NAME"],
                from_bus=bus_map[int(line["FROM_BUS"])],
                to_bus=bus_map[int(line["TO_BUS"])],
                length_km=float(line["LENGTH_KM"]),
                r_ohm_per_km=float(line["R_OHM_PER_KM"]),
                x_ohm_per_km=float(line["X_OHM_PER_KM"]),
                c_nf_per_km=float(line["C_NF_PER_KM"]),
                max_i_ka=float(line["MAX_I_KA"]),
            )
            line_map[int(line["ID"])] = int(index)
        except Exception as e:
            print(line, e)
    # 加载量测数据
    line_measurements = load_measurement_data("api/measurement/line")
    print("Line measurements loaded.")
    print("Line Section completed\n")


def load_static_gen_section(net):
    print("Starting Static Generator Section")
    gen_data = load_element_data("api/static-gen")
    # print('static gen-data', gen_data)
    for gen in gen_data:
        try:
            print(gen["BUS"], gen["NAME"], "static-gen-bus")
            pp.create_sgen(
                net,
                index=int(gen["ID"]),
                name=gen["NAME"],
                bus=bus_map[int(gen["BUS"])],
                p_mw=float(gen["P_MW"]),
                q_mvar=float(gen["Q_MVAR"]),
            )
        except Exception as e:
            print(gen, e)
    # 加载量测数据
    gen_measurements = load_measurement_data("api/measurement/static-gen")
    print("Generator measurements loaded.")
    print("Generator Section completed\n")


def load_gen_section(net):
    print("Starting Generator Section")
    gen_data = load_element_data("api/gen")
    for gen in gen_data:
        try:
            pp.create_gen(
                net,
                index=int(gen["ID"]),
                name=gen["NAME"],
                bus=int(gen["母线ID"]),
                p_mw=float(gen["标称功率"]),
            )
        except Exception as e:
            print(gen, e)
    # 加载量测数据
    gen_measurements = load_measurement_data("api/measurement/gen")
    print("Generator measurements loaded.")
    print("Generator Section completed\n")


def load_load_section(net):
    print("Starting Load Section")
    load_data = load_element_data("api/load")
    for load in load_data:
        pp.create_load(
            net,
            index=load["ID"],
            name=load["NAME"],
            bus=bus_map[int(load["BUS"])],
            p_mw=float(load["P_MW"]),
            q_mvar=float(load["Q_MVAR"]),
        )
    # 加载量测数据
    load_measurements = load_measurement_data("api/measurement/load")
    print("Load measurements loaded.")
    print("Load Section completed\n")


def load_switch_section(net):
    print("Starting Switch Section")
    for switch in load_element_data("api/switch"):
        if switch["ET"] == "b":
            element = bus_map[int(switch["ELEMENT"])]
        elif switch["ET"] == "l":
            element = line_map[int(switch["ELEMENT"])]
        elif switch["ET"] == "t" or switch['ET'] == 't3':
            element = trafo_map[int(switch["ELEMENT"])]
        pp.create_switch(
            net,
            # index=switch["ID"],
            name=switch["NAME"],
            bus=bus_map[int(switch["BUS"])],
            et=switch["ET"],
            element=element,
        )
    # 加载量测数据
    switch_measurements = load_measurement_data("api/measurement/switch")
    print("Switch measurements loaded.")
    print("Switch Section completed\n")


def run_powerflow(net):
    print("Starting Power Flow Calculation")

    # 保存断面数据
    pp.to_sqlite(net, "./net.db")

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
    pp.runpp(net)
    print("Power Flow Completed")


def main():
    # 创建空网络
    net = pp.create_empty_network(name="浙江电网")

    # 环节1：加载母线
    load_bus_section(net)

    # 环节2-5：并行执行
    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(load_trafo_section, net),
            executor.submit(load_line_section, net),
            executor.submit(load_gen_section, net),
            executor.submit(load_load_section, net),
            # executor.submit(load_static_gen_section, net),
        ]

        for future in futures:
            future.result()

    # 环节6：加载开关
    load_switch_section(net)

    pp.create_ext_grid(net, bus=0)

    # 环节7：潮流计算
    run_powerflow(net)


if __name__ == "__main__":
    main()