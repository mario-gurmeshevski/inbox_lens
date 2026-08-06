import subprocess
import re
import os
import sys

def main():
    python_exe = sys.executable
    results = []
    for f in sorted(os.listdir('src/tests')):
        if f.startswith('test_') and f.endswith('.py'):
            out = subprocess.run(
                [python_exe, '-m', 'pytest', f'src/tests/{f}', '--collect-only'],
                capture_output=True, text=True, env=dict(os.environ, PYTHONPATH='src')
            )
            m = re.search(r'collected (\d+)', out.stdout)
            if m:
                results.append((int(m.group(1)), f))

    print(f'Total: {sum(count for count, _ in results)}')
    for count, name in sorted(results, key=lambda x: -x[0]):
        print(f'{name}: {count}')

if __name__ == '__main__':
    main()
