#Requires -Version 5.1
<#
  simulate.ps1 - ClinicFlow End-to-End Simulation
  Run from project root: .\simulate.ps1
  Requires backend running at http://127.0.0.1:8000
#>

param([string]$Base = "http://127.0.0.1:8000")

$Pass = 0; $Fail = 0; $Errors = @()

function Step([string]$Name, [scriptblock]$Test) {
    try {
        $ok = & $Test
        if ($ok -ne $false) {
            Write-Host "  [PASS] $Name" -ForegroundColor Green
            $script:Pass++
        } else {
            Write-Host "  [FAIL] $Name" -ForegroundColor Red
            $script:Fail++
            $script:Errors += $Name
        }
    } catch {
        Write-Host "  [FAIL] $Name -- $($_.Exception.Message)" -ForegroundColor Red
        $script:Fail++
        $script:Errors += $Name
    }
}

function RestPost([string]$Path, [string]$Json) {
    Invoke-RestMethod -Uri "$Base$Path" -Method Post -ContentType "application/json" -Body $Json
}

function RestPatch([string]$Path, [string]$Json) {
    Invoke-RestMethod -Uri "$Base$Path" -Method Patch -ContentType "application/json" -Body $Json
}

function RestGet([string]$Path) {
    Invoke-RestMethod -Uri "$Base$Path"
}

function FormPost([string]$Path, [hashtable]$Fields) {
    Invoke-WebRequest -Uri "$Base$Path" -Method Post -Body $Fields -UseBasicParsing
}

function Expect4xx([string]$Path, [string]$Method, [string]$Json) {
    try {
        if ($Method -eq "POST") {
            Invoke-RestMethod -Uri "$Base$Path" -Method Post -ContentType "application/json" -Body $Json -ErrorAction Stop | Out-Null
        } else {
            Invoke-RestMethod -Uri "$Base$Path" -Method Patch -ContentType "application/json" -Body $Json -ErrorAction Stop | Out-Null
        }
        return $false
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        return ($code -ge 400 -and $code -lt 500)
    }
}

function Expect404([string]$Path) {
    try {
        RestGet $Path | Out-Null
        return $false
    } catch {
        return ($_.Exception.Response.StatusCode.value__ -eq 404)
    }
}

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  ClinicFlow Simulation -- Step-by-Step Validation" -ForegroundColor Cyan
Write-Host "  Base URL: $Base" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "PHASE 1 - MINIMUM -- Health and Database" -ForegroundColor Yellow

$health = $null
Step "Health endpoint returns status ok" {
    $script:health = RestGet "/health"
    $script:health.status -eq "ok"
}
Step "Health reports db = ok" {
    $script:health.db -eq "ok"
}
Step "Health returns version field" {
    $null -ne $script:health.version
}

Write-Host ""
Write-Host "PHASE 2 - LEADS -- Create, Read, Validate" -ForegroundColor Yellow

$lead1 = $null
Step "Create lead -- valid web source" {
    $script:lead1 = RestPost "/api/leads" '{"full_name":"Ana Rivera","phone":"+17875559876","email":"ana@example.com","service":"Cleaning","preferred_time":"Tomorrow 10:00 AM","source":"web"}'
    $script:lead1.id -gt 0 -and $script:lead1.status -eq "new"
}
$lead2 = $null
Step "Create lead -- missed call source" {
    $script:lead2 = RestPost "/api/leads" '{"full_name":"Carlos Medina","phone":"7875550001","service":"Emergency","preferred_time":"ASAP","source":"missed_call"}'
    $script:lead2.source -eq "missed_call"
}
Step "Create lead -- 120 char name" {
    $name = "A" * 120
    $body = '{"full_name":"' + $name + '","phone":"+17875550002","service":"Cleaning","preferred_time":"TBD","source":"web"}'
    $r = RestPost "/api/leads" $body
    $r.id -gt 0
}
Step "Validation -- missing full_name returns 422" {
    Expect4xx "/api/leads" "POST" '{"phone":"+17875559999","service":"Cleaning","preferred_time":"TBD","source":"web"}'
}
Step "Validation -- name too short returns 422" {
    Expect4xx "/api/leads" "POST" '{"full_name":"A","phone":"+17875559999","service":"Cleaning","preferred_time":"TBD","source":"web"}'
}
Step "Validation -- phone too short returns 422" {
    Expect4xx "/api/leads" "POST" '{"full_name":"Test Patient","phone":"123","service":"Cleaning","preferred_time":"TBD","source":"web"}'
}
Step "Validation -- invalid email returns 422" {
    Expect4xx "/api/leads" "POST" '{"full_name":"Test Patient","phone":"+17875559999","email":"not-an-email","service":"Cleaning","preferred_time":"TBD","source":"web"}'
}
Step "Validation -- invalid source value returns 422" {
    Expect4xx "/api/leads" "POST" '{"full_name":"Test Patient","phone":"+17875559999","service":"Cleaning","preferred_time":"TBD","source":"fax"}'
}
Step "List leads -- returns 2 or more items" {
    $r = RestGet "/api/leads"
    $r.items.Count -ge 2
}
Step "PATCH lead status -- contacted" {
    $r = RestPatch "/api/leads/$($script:lead1.id)" '{"status":"contacted"}'
    $r.status -eq "contacted"
}
Step "PATCH lead status -- no_show" {
    $r = RestPatch "/api/leads/$($script:lead2.id)" '{"status":"no_show"}'
    $r.status -eq "no_show"
}
Step "PATCH lead status -- invalid value returns 422" {
    Expect4xx "/api/leads/$($script:lead1.id)" "PATCH" '{"status":"rescheduled"}'
}
Step "PATCH lead status -- non-existent lead returns 404" {
    try {
        RestPatch "/api/leads/999999" '{"status":"booked"}' | Out-Null
        $false
    } catch {
        $_.Exception.Response.StatusCode.value__ -eq 404
    }
}

Write-Host ""
Write-Host "PHASE 3 - KPI -- Counts and Revenue" -ForegroundColor Yellow

$kpi = $null
Step "KPI endpoint returns all required fields" {
    $script:kpi = RestGet "/api/kpi"
    ($null -ne $script:kpi.total_leads) -and ($null -ne $script:kpi.new) -and
    ($null -ne $script:kpi.booked) -and ($null -ne $script:kpi.no_show) -and
    ($null -ne $script:kpi.estimated_missed_revenue)
}
Step "KPI total_leads >= 2" {
    $script:kpi.total_leads -ge 2
}
Step "KPI no_show >= 1" {
    $script:kpi.no_show -ge 1
}
Step "KPI missed_revenue = new * 180" {
    $script:kpi.estimated_missed_revenue -eq ($script:kpi.new * 180)
}

Write-Host ""
Write-Host "PHASE 4 - BOOKINGS -- Create, List, ICS" -ForegroundColor Yellow

$booking = $null
$futureTime = (Get-Date).AddDays(1).ToString("yyyy-MM-ddTHH:mm:ss")

Step "Create booking -- valid 30 min" {
    $body = '{"lead_id":' + $script:lead1.id + ',"start_time":"' + $futureTime + '","duration_minutes":30,"notes":"First visit","push_to_google":false}'
    $r = RestPost "/api/bookings" $body
    $script:booking = $r.booking
    $r.booking.id -gt 0 -and $r.sms.status -eq "stubbed" -and $r.google.status -eq "skipped"
}
Step "Create booking -- Google push stub returns stubbed" {
    $ft2 = (Get-Date).AddDays(2).ToString("yyyy-MM-ddTHH:mm:ss")
    $b2 = '{"lead_id":' + $script:lead1.id + ',"start_time":"' + $ft2 + '","duration_minutes":45,"push_to_google":true}'
    $r2 = RestPost "/api/bookings" $b2
    $r2.google.status -eq "stubbed"
}
Step "Create booking -- invalid lead_id returns 404" {
    try {
        RestPost "/api/bookings" ('{"lead_id":999999,"start_time":"' + $futureTime + '","duration_minutes":30}') | Out-Null
        $false
    } catch {
        $_.Exception.Response.StatusCode.value__ -eq 404
    }
}
Step "Create booking -- duration too short returns 422" {
    Expect4xx "/api/bookings" "POST" ('{"lead_id":' + $script:lead1.id + ',"start_time":"' + $futureTime + '","duration_minutes":5}')
}
Step "Create booking -- duration too long returns 422" {
    Expect4xx "/api/bookings" "POST" ('{"lead_id":' + $script:lead1.id + ',"start_time":"' + $futureTime + '","duration_minutes":300}')
}
Step "Create booking -- past start_time returns 422" {
    $past = (Get-Date).AddDays(-1).ToString("yyyy-MM-ddTHH:mm:ss")
    Expect4xx "/api/bookings" "POST" ('{"lead_id":' + $script:lead1.id + ',"start_time":"' + $past + '","duration_minutes":30}')
}
Step "List bookings -- returns at least 1 item" {
    $r = RestGet "/api/bookings"
    $r.items.Count -ge 1
}
Step "Booking auto-sets lead status to booked" {
    $r = RestGet "/api/leads"
    $upd = $r.items | Where-Object { $_.id -eq $script:lead1.id }
    $upd.status -eq "booked"
}
Step "ICS download -- contains BEGIN:VCALENDAR" {
    $r = Invoke-WebRequest -Uri "$Base/api/bookings/$($script:booking.id).ics" -UseBasicParsing
    $r.Content -match "BEGIN:VCALENDAR"
}
Step "ICS DTSTART has Z UTC suffix" {
    $r = Invoke-WebRequest -Uri "$Base/api/bookings/$($script:booking.id).ics" -UseBasicParsing
    $r.Content -match "DTSTART:\d{8}T\d{6}Z"
}
Step "ICS DTEND has Z UTC suffix" {
    $r = Invoke-WebRequest -Uri "$Base/api/bookings/$($script:booking.id).ics" -UseBasicParsing
    $r.Content -match "DTEND:\d{8}T\d{6}Z"
}
Step "ICS SUMMARY contains patient name" {
    $r = Invoke-WebRequest -Uri "$Base/api/bookings/$($script:booking.id).ics" -UseBasicParsing
    $r.Content -match "SUMMARY:.*Ana Rivera"
}
Step "ICS non-existent booking returns 404" {
    Expect404 "/api/bookings/999999.ics"
}

Write-Host ""
Write-Host "PHASE 5 - SMS -- Direct Send and Reminders" -ForegroundColor Yellow

Step "SMS send -- stub mode returns stubbed" {
    $r = RestPost "/api/sms/send" ('{"to":"+17875559876","body":"Test message","lead_id":' + $script:lead1.id + '}')
    $r.status -eq "stubbed"
}
Step "SMS send -- missing to field returns 422" {
    Expect4xx "/api/sms/send" "POST" '{"body":"Hello"}'
}
Step "Reminders run -- returns reminders_sent and follow_ups_sent" {
    $r = RestPost "/api/sms/reminders/run" '{}'
    ($null -ne $r.reminders_sent) -and ($null -ne $r.follow_ups_sent)
}
Step "Reminders follow_ups_sent >= 1 (no_show lead)" {
    $r = RestPost "/api/sms/reminders/run" '{}'
    $r.follow_ups_sent -ge 1
}

Write-Host ""
Write-Host "PHASE 6 - TWILIO WEBHOOKS -- Voice and SMS" -ForegroundColor Yellow

Step "Twilio voice -- returns TwiML with Say verb" {
    $r = FormPost "/twilio/voice" @{ From = "+17875551111"; To = "+17875550000"; CallStatus = "no-answer" }
    ($r.Content -match "<Response>") -and ($r.Content -match "<Say")
}
Step "Twilio voice -- creates missed_call lead" {
    $before = (RestGet "/api/leads").items.Count
    FormPost "/twilio/voice" @{ From = "+17875552222"; To = "+17875550000"; CallStatus = "no-answer" } | Out-Null
    (RestGet "/api/leads").items.Count -gt $before
}
Step "Twilio voice -- logs auto-SMS in messages" {
    $before = (RestGet "/api/messages").items.Count
    FormPost "/twilio/voice" @{ From = "+17875553333"; To = "+17875550000"; CallStatus = "no-answer" } | Out-Null
    (RestGet "/api/messages").items.Count -gt $before
}
Step "Twilio SMS -- BOOK keyword" {
    $r = FormPost "/twilio/sms" @{ From = "+17875551111"; Body = "BOOK please" }
    $r.Content -match "book"
}
Step "Twilio SMS -- C confirm keyword" {
    $r = FormPost "/twilio/sms" @{ From = "+17875551111"; Body = "C" }
    $r.Content -match "onfirm"
}
Step "Twilio SMS -- R reschedule keyword" {
    $r = FormPost "/twilio/sms" @{ From = "+17875551111"; Body = "R" }
    $r.Content -match "reschedule"
}
Step "Twilio SMS -- STOP opt-out keyword" {
    $r = FormPost "/twilio/sms" @{ From = "+17875551111"; Body = "STOP" }
    $r.Content -match "unsubscribed"
}
Step "Twilio SMS -- START re-subscribe keyword" {
    $r = FormPost "/twilio/sms" @{ From = "+17875551111"; Body = "START" }
    $r.Content -match "subscribed"
}
Step "Twilio SMS -- unknown input gets default TwiML" {
    $r = FormPost "/twilio/sms" @{ From = "+17875551111"; Body = "hello" }
    $r.Content -match "<Response>"
}
Step "Twilio SMS -- inbound messages logged" {
    $msgs = (RestGet "/api/messages").items
    ($msgs | Where-Object { $_.direction -eq "in" }).Count -ge 1
}

Write-Host ""
Write-Host "PHASE 7 - MESSAGES -- Log Integrity" -ForegroundColor Yellow

Step "Messages endpoint returns items array" {
    $r = RestGet "/api/messages"
    $r.items -is [array]
}
Step "Messages have outbound booking confirmation" {
    $msgs = (RestGet "/api/messages").items
    ($msgs | Where-Object { $_.direction -eq "out" -and $_.body -match "visit is confirmed" }).Count -ge 1
}
Step "Messages have missed-call auto-SMS" {
    $msgs = (RestGet "/api/messages").items
    ($msgs | Where-Object { $_.direction -eq "out" -and $_.body -match "calling our dental" }).Count -ge 1
}

Write-Host ""
Write-Host "PHASE 8 - MAXIMUM -- Edge Cases and Security" -ForegroundColor Yellow

Step "Health still ok after all write tests" {
    (RestGet "/health").status -eq "ok"
}
Step "Lead with special chars in name succeeds" {
    $r = RestPost "/api/leads" '{"full_name":"Dr. O Reilly Jr.","phone":"+17875558888","service":"Implants","preferred_time":"TBD","source":"web"}'
    $r.id -gt 0
}
Step "KPI total_leads >= 5 after full test run" {
    (RestGet "/api/kpi").total_leads -ge 5
}
Step "JSON endpoints return application/json" {
    $r = Invoke-WebRequest -Uri "$Base/api/leads" -UseBasicParsing
    $r.Headers["Content-Type"] -match "application/json"
}
Step "ICS endpoint returns text/calendar" {
    $r = Invoke-WebRequest -Uri "$Base/api/bookings/$($script:booking.id).ics" -UseBasicParsing
    $r.Headers["Content-Type"] -match "text/calendar"
}
Step "ICS with long notes is valid VCALENDAR" {
    $ft4 = (Get-Date).AddDays(4).ToString("yyyy-MM-ddTHH:mm:ss")
    $bSpec = '{"lead_id":' + $script:lead1.id + ',"start_time":"' + $ft4 + '","duration_minutes":30,"notes":"This is a long test note for the appointment"}'
    $bk = (RestPost "/api/bookings" $bSpec).booking
    $r = Invoke-WebRequest -Uri "$Base/api/bookings/$($bk.id).ics" -UseBasicParsing
    $r.Content -match "BEGIN:VCALENDAR"
}
Step "Twilio voice with empty From returns TwiML" {
    $r = FormPost "/twilio/voice" @{ From = ""; To = ""; CallStatus = "completed" }
    $r.Content -match "<Response>"
}
Step "Lead with 100-char name stores successfully" {
    $big = "B" * 100
    $r = RestPost "/api/leads" ('{"full_name":"' + $big + '","phone":"+17875559111","service":"Implants","preferred_time":"TBD","source":"web"}')
    $r.id -gt 0
}

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  RESULTS" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
$total = $Pass + $Fail
$pct   = if ($total -gt 0) { [math]::Round(($Pass / $total) * 100, 1) } else { 0 }
$color = if ($Fail -eq 0) { "Green" } else { "Yellow" }
Write-Host "  Passed : $Pass / $total  ($pct%)" -ForegroundColor $color
if ($Fail -gt 0) {
    Write-Host "  Failed : $Fail" -ForegroundColor Red
    $Errors | ForEach-Object { Write-Host "    x $_" -ForegroundColor Red }
} else {
    Write-Host "  All tests passed!" -ForegroundColor Green
}
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

exit $Fail
