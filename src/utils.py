import os
import xlrd
import xlwt
from xlutils.copy import copy
from datetime import datetime

def save_tickets_to_excel(task):
    """
    将操作票保存到Excel文件
    
    Args:
        tickets: 操作票列表，每个元素包含unit、operation、action字段
    """
    tickets = task["tickets"]
    description = task["description"]
    if not tickets:
        print("没有操作票需要保存")
        return
    
    # 确保result目录存在
    result_dir = "result"
    if not os.path.exists(result_dir):
        os.makedirs(result_dir)
    
    # 先复制模板文件为临时文件
    template_path = os.path.join(result_dir, "template_ops_ticket.xls")
    excel_path = os.path.join(result_dir, "temp_ops_ticket.xls")
    
    import shutil
    if os.path.exists(template_path):
        shutil.copy2(template_path, excel_path)
        print(f"已复制模板文件: {template_path} -> {excel_path}")
    else:
        print(f"警告: 模板文件 {template_path} 不存在，将使用现有文件或创建新文件")
    try:
        # 读取现有的Excel文件
        rb = xlrd.open_workbook(excel_path, formatting_info=True)
        wb = copy(rb)
        ws = wb.get_sheet(0)
        ws.write(1,2,"事故处置")
        ws.write(3,2,description)
        ws.write(4,2,"施云辉")
        ws.write(4,5,datetime.now().strftime("%Y-%m-%d"))
        ws.write(4,8,datetime.now().strftime("%Y-%m-%d"))
        # 找到操作内容开始的行（从第6行开始插入操作票）
        start_row = 6  # 第6行（0-based索引）
        
        # 插入操作票数据
        for i, ticket in enumerate(tickets):
            row_num = start_row + i
            # 序号列（A列）
            ws.write(row_num, 0, str(i + 1))
            # 受令单位（B列，BC合并）
            ws.write(row_num, 1, ticket.get('unit', ''))
            # 操作内容（D列，DEF合并）
            ws.write(row_num, 3, ticket.get('operation', ''))
            # 其他列可以根据需要填写
            # C列为受令单位的合并部分，不需要单独写入
            # E、F列为操作内容的合并部分，不需要单独写入
            
        # 保存文件
        wb.save(excel_path)
        print(f"操作票已保存到: {excel_path}")
        
    except Exception as e:
        print(f"保存操作票到Excel时发生错误: {str(e)}")
        # 如果读取失败，创建新的Excel文件