#!/usr/bin/env python3
"""
Test runner script for TaskPilotAgent tests
"""
import subprocess
import sys
from pathlib import Path

def run_tests():
    """Run all tests using pytest"""
    project_root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run([
            sys.executable, "-m", "pytest", 
            "-v", 
            "--tb=short",
            "tests/"
        ], cwd=project_root)
        
        return result.returncode == 0
    except FileNotFoundError:
        print("Error: pytest not found. Please install pytest with:")
        print("pip install pytest pytest-mock")
        return False

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
