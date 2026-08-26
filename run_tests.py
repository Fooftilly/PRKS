#!/usr/bin/env python3
import unittest
import sys
import os


def apply_isolated_test_env(project_dir: str) -> None:
    os.environ["PRKS_TESTING"] = "1"
    os.environ["PRKS_STORAGE"] = os.path.join(project_dir, "data_testing")
    os.environ.pop("PRKS_FOR_PROCESSING_DIR", None)
    os.environ.pop("PRKS_LOG_FILE", None)


def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    apply_isolated_test_env(project_dir)

    print("Discovering and running tests...")
    
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(project_dir, 'tests'), pattern='test_*.py')
    
    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Return exit code based on test success
    if result.wasSuccessful():
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == '__main__':
    main()
