@echo off
REM Fix WSL Hyper-V firewall so Windows can reach ports 8080,8101,8160,3306,6666
REM Run as Administrator (right-click -> Run as administrator)

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please right-click this file and choose "Run as administrator".
    pause
    exit /b 1
)

echo [1/3] Trying New-NetFirewallHyperVRule ...
powershell -NoProfile -Command "New-NetFirewallHyperVRule -DisplayName 'WSL2-GamePorts' -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 8080,8101,8160,3306,6666 -Action Allow" && goto :ok

echo [2/3] Fallback: Set-NetFirewallHyperVVMSetting DefaultInboundAction=Allow ...
powershell -NoProfile -Command "Set-NetFirewallHyperVVMSetting -Name '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -DefaultInboundAction Allow" && goto :ok

echo [3/3] Fallback: classic firewall rule for WSL subnet ...
powershell -NoProfile -Command "New-NetFirewallRule -DisplayName 'WSL2-Subnet-Allow' -Direction Inbound -Action Allow -Protocol TCP -RemoteAddress 172.16.0.0/12" && goto :ok

echo [FAIL] All methods failed. Please report the error message above.
pause
exit /b 1

:ok
echo.
echo [OK] Firewall rule added. Ports 8080/8101/8160/3306/6666 are now reachable.
pause
