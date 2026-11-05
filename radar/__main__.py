import sys

from radar.cli import main

# Windows 콘솔 기본 인코딩(cp949)에서 출력이 깨지거나 죽지 않게 한다
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
raise SystemExit(main())
