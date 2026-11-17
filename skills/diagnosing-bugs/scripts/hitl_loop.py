#!/usr/bin/env python3
"""
跨平台 Human-in-the-loop (HITL) 缺陷复现与信息采集脚本。
支持 Windows、Linux 与 macOS 原生终端执行。
"""
import sys

def step(instruction: str):
    print(f"\n>>> {instruction}")
    input("    [按 Enter 继续] ")

def capture(var_name: str, question: str) -> str:
    print(f"\n>>> {question}")
    answer = input("    > ").strip()
    return answer

def main():
    print("=== 开始人工引导缺陷排查复现流程 ===")
    step("在浏览器中打开目标页面或服务，并准备复现操作。")
    errored = capture("ERRORED", "触发目标操作时是否发生了报错？(y/n)")
    error_msg = capture("ERROR_MSG", "请粘贴控制台或日志中的具体报错信息 (若无填 'none'):")
    
    print("\n--- 采集到的关键排查证据 ---")
    print(f"ERRORED={errored}")
    print(f"ERROR_MSG={error_msg}")

if __name__ == "__main__":
    main()
