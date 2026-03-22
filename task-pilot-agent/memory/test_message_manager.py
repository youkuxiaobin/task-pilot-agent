#!/usr/bin/env python3
"""
测试MessageManager功能
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from memory_mgr import MemoryManager

def test_message_operations():
    """测试消息操作"""
    print("开始测试MessageManager...")
    
    # 创建MemoryManager实例
    memory_manager = MemoryManager()
    
    # 测试添加消息
    print("\n1. 测试添加消息...")
    trace_id1 = memory_manager.add_message(
        user_id="user123",
        conversation_id="conv456",
        agent_id="agent789",
        role="user",
        content="你好，这是一个测试消息"
    )
    print(f"添加消息成功，trace_id: {trace_id1}")
    
    trace_id2 = memory_manager.add_message(
        user_id="user123",
        conversation_id="conv456",
        agent_id="agent789",
        role="assistant",
        content="你好！我是AI助手，很高兴为您服务。"
    )
    print(f"添加消息成功，trace_id: {trace_id2}")
    
    # 测试根据trace_id获取消息
    print("\n2. 测试根据trace_id获取消息...")
    messages = memory_manager.get_messages(trace_id=trace_id1)
    print(f"根据trace_id获取到 {len(messages)} 条消息:")
    for msg in messages:
        print(f"  - {msg['role']}: {msg['content']}")
    
    # 测试根据用户ID获取消息
    print("\n3. 测试根据用户ID获取消息...")
    messages = memory_manager.get_messages(user_id="user123")
    print(f"根据用户ID获取到 {len(messages)} 条消息:")
    for msg in messages:
        print(f"  - {msg['role']}: {msg['content']}")
    
    # 测试根据会话ID获取消息
    print("\n4. 测试根据会话ID获取消息...")
    messages = memory_manager.get_messages(conversation_id="conv456")
    print(f"根据会话ID获取到 {len(messages)} 条消息:")
    for msg in messages:
        print(f"  - {msg['role']}: {msg['content']}")
    
    # 测试更新消息
    print("\n5. 测试更新消息...")
    success = memory_manager.update_message(
        trace_id=trace_id1,
        content="这是更新后的消息内容"
    )
    print(f"更新消息结果: {success}")
    
    # 验证更新结果
    messages = memory_manager.get_messages(trace_id=trace_id1)
    if messages:
        print(f"更新后的消息内容: {messages[0]['content']}")
    
    # 测试删除消息
    print("\n6. 测试删除消息...")
    success = memory_manager.delete_message(trace_id=trace_id2)
    print(f"删除消息结果: {success}")
    
    # 验证删除结果
    messages = memory_manager.get_messages(trace_id=trace_id2)
    print(f"删除后根据trace_id获取到 {len(messages)} 条消息")
    
    print("\n测试完成！")

if __name__ == "__main__":
    test_message_operations()
