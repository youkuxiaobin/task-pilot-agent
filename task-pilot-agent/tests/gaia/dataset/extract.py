import json
import re

# 输入文件路径
input_file = "metadata.jsonl"
# 输出文件路径
output_file = "attachment.txt"

# 用于清除控制字符的正则表达式
control_characters_regex = re.compile(r'[\x00-\x1F\x7F]')

# 函数：清理控制字符
def clean_control_characters(text):
    return control_characters_regex.sub('', text)

# 打开输入文件并处理
with open(input_file, 'r', encoding='utf-8') as infile, open(output_file, 'w', encoding='utf-8') as outfile:
    for line in infile:
        try:
            # 解析每一行 JSON
            data = json.loads(line)
            
            # 清理 "Question" 和 "file_name" 字段中的控制字符
            question = clean_control_characters(data.get('Question', ''))
            file_name = clean_control_characters(data.get('file_name', ''))
            task_id = clean_control_characters(data.get('task_id', ''))
            if file_name == "":
                continue

            # 写入输出文件
            outfile.write(f"Question: {question}\n")
            outfile.write(f"File Name: {file_name}\n")
            outfile.write(f"task_id: {task_id}\n")
            outfile.write("---------------------------------\n")
        
        except json.JSONDecodeError as e:
            print(f"Error parsing line: {line}\nError: {e}")

print(f"Extraction complete. Output saved to {output_file}.")

