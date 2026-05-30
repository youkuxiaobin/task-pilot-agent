#!/usr/bin/env python3
"""
Comprehensive test runner for all memory module tests
"""
import pytest
import sys
import os

# Add the parent directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))

def run_memory_tests():
    """Run all memory module tests"""
    test_files = [
        "test_memory_mgr_simple.py",
        "test_memory_degradation.py",
        "test_rag_retriever.py"
    ]
    
    test_paths = [os.path.join(os.path.dirname(__file__), f) for f in test_files]
    
    # Run pytest with all test files
    result = pytest.main([
        "-v",
        "--tb=short",
        "--color=yes",
        "--durations=10"
    ] + test_paths)
    
    return result == 0

if __name__ == "__main__":
    success = run_memory_tests()
    sys.exit(0 if success else 1)
