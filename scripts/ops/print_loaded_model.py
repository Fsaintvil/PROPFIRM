#!/usr/bin/env python3
import runpy
from pathlib import Path


def main():
    # Execute the module in-process to access MetaLearningTradingSystem class
    mod = runpy.run_path(str(Path('meta_learning_system.py')))
    MetaLearningTradingSystem = mod.get('MetaLearningTradingSystem')
    if MetaLearningTradingSystem is None:
        print('MetaLearningTradingSystem not found in meta_learning_system.py')
        return
    m = MetaLearningTradingSystem()
    print('loaded_model_path=', getattr(m, 'loaded_model_path', None))


if __name__ == '__main__':
    main()
