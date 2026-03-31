#!/bin/bash
truncate -s 0 /var/log/gap.log
truncate -s 0 cd /root/NeoGap/gap.log
cd /root/NeoGap
source venv/bin/activate
python3.11 -u main.py run 2>&1 | tee -a gap.log