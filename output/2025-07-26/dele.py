import json

def remove_fields_from_json(input_file, output_file, fields_to_remove):
  """
  从JSON文件中读取数据，删除指定的字段后，写入新的JSON文件。

  Args:
    input_file: 输入的JSON文件名。
    output_file: 输出的JSON文件名。
    fields_to_remove: 一个包含要删除的字段名的列表。
  """
  try:
    with open(input_file, 'r', encoding='utf-8') as f:
      data = json.load(f)

    for item in data:
      for field in fields_to_remove:
        if field in item:
          del item[field]

    with open(output_file, 'w', encoding='utf-8') as f:
      json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"已成功处理文件，并将结果保存在 '{output_file}'")

  except FileNotFoundError:
    print(f"错误: 输入文件 '{input_file}' 未找到。")
  except json.JSONDecodeError:
    print(f"错误: 输入文件 '{input_file}' 不是有效的JSON格式。")
  except Exception as e:
    print(f"处理过程中发生错误: {e}")

# --- 使用示例 ---
# 您需要将 '2025-07-26_公积金.json' 替换成您的实际文件名
input_filename = '2025-07-26_公积金.json'
output_filename = '2025-07-26_公积金_modified.json'
fields_to_delete = ["content", "wordcount"]

remove_fields_from_json(input_filename, output_filename, fields_to_delete)