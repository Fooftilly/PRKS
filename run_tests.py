#!/usr/bin/env python3
import unittest
import sys
import os

def main():
    # Ensure the PRKS directory is in sys.path
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    # Tests must never touch container storage (/data). Force test mode and
    # redirect any PRKS_STORAGE-based paths into data_testing/.
    os.environ.setdefault("PRKS_TESTING", "1")
    os.environ.setdefault("PRKS_STORAGE", os.path.join(project_dir, "data_testing"))
        
    print("Discovering and running tests...")
    
    # Discover tests in the 'tests' directory
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
