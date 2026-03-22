#!/usr/bin/env python3
"""
MessageManager使用示例
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from memory_mgr import MemoryManager

def main():
    """主函数"""
    print("MessageManager使用示例")
    print("=" * 50)
    
    # 创建MemoryManager实例
    memory_manager = MemoryManager()
    
    # 示例1: 添加用户消息
    print("\n1. 添加用户消息...")
    user_trace_id = memory_manager.add_message(
        user_id="user_001",
        conversation_id="conv_001", 
        agent_id="agent_001",
        role="user",
        content="请帮我分析一下这个数据文件"
    )
    print(f"用户消息已添加，trace_id: {user_trace_id}")
    
    # 示例2: 添加助手回复
    print("\n2. 添加助手回复...")
    assistant_trace_id = memory_manager.add_message(
        user_id="user_001",
        conversation_id="conv_001",
        agent_id="agent_001", 
        role="assistant",
        content="好的，我来帮您分析数据文件。请提供文件路径或上传文件。"
    )
    print(f"助手回复已添加，trace_id: {assistant_trace_id}")
    
    # 示例3: 查询特定会话的所有消息
    print("\n3. 查询会话消息...")
    messages = memory_manager.get_messages(conversation_id="conv_001")
    print(f"会话 conv_001 共有 {len(messages)} 条消息:")
    for i, msg in enumerate(messages, 1):
        print(f"  {i}. [{msg['role']}] {msg['content']}")
        print(f"     时间: {msg['create_time']}, trace_id: {msg['trace_id']}")
    
    # 示例4: 查询特定用户的所有消息
    print("\n4. 查询用户消息...")
    user_messages = memory_manager.get_messages(user_id="user_001")
    print(f"用户 user_001 共有 {len(user_messages)} 条消息")
    
    # 示例5: 更新消息内容
    print("\n5. 更新消息内容...")
    success = memory_manager.update_message(
        trace_id=assistant_trace_id,
        content="好的，我来帮您分析数据文件。请提供文件路径或上传文件。我会使用Python pandas库进行分析。"
    )
    print(f"消息更新结果: {success}")
    
    # 验证更新
    updated_msg = memory_manager.get_messages(trace_id=assistant_trace_id)
    if updated_msg:
        print(f"更新后的内容: {updated_msg[0]['content']}")
    
    print("\n示例完成！")

if __name__ == "__main__":
    main()
