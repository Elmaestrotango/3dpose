# Spread GigE receive processing across cores on the camera NIC ports.
#
# WHY (measured 2026-09-03, docs/PERF_EXPERIMENTS.md E5):
#   Both camera ports report NumberOfReceiveQueues = 1, so RSS is enabled but
#   inert: each port's ~78,000 packets/s funnel through a single core's DPC.
#   During a 150 s 6-camera recording, cores 0 and 1 sat at 45.7% and 45.4%
#   DPC time while the 24-core average was 3.96%. Ethernet 5 discarded 35,423
#   packets at the NIC over that run; Ethernet 4 discarded zero. UDPv4 receive
#   errors were 0 (so it is not socket-buffer overflow) and packet errors were 0
#   (so it is not corruption on the wire) -- what is left is the receive ring
#   being serviced too slowly, i.e. host-side scheduling.
#
#   Frame loss today is already near zero because pylon's resends recover those
#   discards. The point of this change is MARGIN FOR 9 CAMERAS: a third port
#   adds a third DPC-bound core, and 46% is not where you want to begin a 50%
#   increase in packet rate.
#
# WHY IT SHOULD WORK: Windows hashes non-TCP IPv4 on the source/destination
#   2-tuple, and the three cameras on each port have distinct IPs, so they
#   should land on different queues. Some Intel drivers ignore the setting for
#   non-TCP traffic, which is why this script VERIFIES rather than assumes.
#
# REVERTING: re-run with -Queues 1.
#
# Applying this RESETS both adapters, so the cameras briefly disappear and
# re-enumerate. Never run it during a recording.
#
# Run ELEVATED:
#   powershell -ExecutionPolicy Bypass -File configure_nic.ps1
[CmdletBinding()]
param(
    [string[]] $Ports  = @("Ethernet 4", "Ethernet 5"),
    [int]      $Queues = 4
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "This must run elevated (Set-NetAdapterRss needs admin)." -ForegroundColor Red
    Write-Host "Right-click PowerShell -> Run as administrator, then re-run." -ForegroundColor Red
    exit 1
}

function Show-State($label) {
    Write-Host ""
    Write-Host "=== $label ===" -ForegroundColor Cyan
    Get-NetAdapterRss -Name $Ports |
        Select-Object Name, Enabled, NumberOfReceiveQueues,
                      MaxProcessorNumber, BaseProcessorNumber |
        Format-Table -AutoSize
}

Show-State "BEFORE"

foreach ($p in $Ports) {
    try {
        Set-NetAdapterRss -Name $p -NumberOfReceiveQueues $Queues -ErrorAction Stop
        Write-Host ("  {0}: requested {1} receive queues" -f $p, $Queues) -ForegroundColor Green
    } catch {
        Write-Host ("  {0}: FAILED -- {1}" -f $p, $_.Exception.Message) -ForegroundColor Red
    }
}

# The adapter reset is not instant; give it a moment before reading back.
Start-Sleep -Seconds 5
Show-State "AFTER"

# Verify rather than assume: a driver that silently ignores the request is the
# expected failure mode here, not an error.
$bad = @()
foreach ($r in (Get-NetAdapterRss -Name $Ports)) {
    if ($r.NumberOfReceiveQueues -lt $Queues) { $bad += $r.Name }
}
Write-Host ""
if ($bad.Count -eq 0) {
    Write-Host "OK: every port reports $Queues receive queues." -ForegroundColor Green
    Write-Host "Next: re-run the acquisition and compare Eth5 ReceivedDiscardedPackets"
    Write-Host "(baseline 35,423 per 150 s) and % DPC Time on cores 0/1 (baseline ~46%)."
} else {
    Write-Host ("NOT APPLIED on: {0}" -f ($bad -join ", ")) -ForegroundColor Yellow
    Write-Host "The driver accepted the call but kept fewer queues -- this happens when"
    Write-Host "a driver only applies RSS to TCP. Fallback is to tune *RssBaseProcNumber"
    Write-Host "and *MaxRssProcessors via Set-NetAdapterAdvancedProperty instead."
}

Write-Host ""
Write-Host "Camera link state (should be Up on both):" -ForegroundColor Cyan
Get-NetAdapter -Name $Ports | Select-Object Name, Status, LinkSpeed | Format-Table -AutoSize
