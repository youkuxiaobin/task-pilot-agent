from memory import memory_manager

if __name__ == "__main__":
    messages = [{
        "role": "user",
        "content": "the weather of beijing"
    }]
    memory_manager.add_memory(messages, "aa", "aa", "aa")
    print(memory_manager.get_memory(user_id="aa"))


    memory_manager.add_plan(["111", "222", "333"], "aa", "bb", "cc")