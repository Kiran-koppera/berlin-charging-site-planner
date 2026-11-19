Attribute VB_Name = "ScenarioRunner"
'==============================================================================
' ScenarioRunner - macros for the HPC charging site business case
'
' RunAllScenarios  (Ctrl+Shift+R)
'   Switches the scenario dropdown on Assumptions!C3 through Pessimistic, Base
'   and Optimistic, recalculates the live model each time and writes one
'   comparison table to the "Scenario results" sheet: NPV, IRR and payback of
'   one site, how many shortlisted sites have a positive NPV, and every site's
'   NPV in each scenario. The original scenario is restored afterwards.
'
' BreakEvenPrice  (Ctrl+Shift+B)
'   Uses Goal Seek to find the net charging price at which one site's NPV is
'   zero in the selected scenario, reports it and restores the input.
'
' Both macros only read and write cells in this workbook.
'==============================================================================
Option Explicit

Private Const SCENARIO_CELL As String = "C3"
Private Const SCENARIO_INDEX_CELL As String = "D3"
Private Const NPV_CELL As String = "B25"
Private Const IRR_CELL As String = "B26"
Private Const PAYBACK_CELL As String = "B27"
Private Const FIRST_SITE_ROW As Long = 5
Private Const SITE_NPV_COL As Long = 9
Private Const RESULTS_SHEET As String = "Scenario results"

Public Sub RunAllScenarios()
Attribute RunAllScenarios.VB_ProcData.VB_Invoke_Func = "R\n14"
    Dim wsA As Worksheet, wsC As Worksheet, wsS As Worksheet, wsR As Worksheet
    Dim scenarios As Variant, original As String
    Dim i As Long, r As Long, lastSite As Long, nSites As Long, positive As Long
    Dim siteNpv As Double, total As Double

    Set wsA = ThisWorkbook.Worksheets("Assumptions")
    Set wsC = ThisWorkbook.Worksheets("Cash flow")
    Set wsS = ThisWorkbook.Worksheets("Sites")
    Set wsR = ResultsSheet()
    scenarios = Array("Pessimistic", "Base", "Optimistic")
    original = CStr(wsA.Range(SCENARIO_CELL).Value)

    lastSite = wsS.Cells(wsS.Rows.Count, 1).End(xlUp).Row
    nSites = lastSite - FIRST_SITE_ROW + 1
    If nSites < 1 Then
        MsgBox "No shortlisted sites found on the Sites sheet.", vbExclamation
        Exit Sub
    End If

    Application.ScreenUpdating = False
    On Error GoTo Restore

    wsR.Cells.Clear
    wsR.Range("A1").Value = "Scenario comparison - one HPC site and the shortlist"
    wsR.Range("A1").Font.Bold = True
    wsR.Range("A1").Font.Size = 13
    wsR.Range("A2").Value = "Written by the RunAllScenarios macro on " & Format(Now, "dd mmm yyyy hh:nn")

    wsR.Range("A4:D4").Value = Array("Metric", "Pessimistic", "Base", "Optimistic")
    wsR.Range("A5:A9").Value = Application.WorksheetFunction.Transpose(Array( _
        "NPV, one site (EUR)", "IRR, one site", "Payback (years)", _
        "Shortlisted sites with NPV > 0", "Total NPV of the shortlist (EUR)"))

    r = 12
    wsR.Range("A11:F11").Value = Array("Rank", "Location", "District", "NPV Pessimistic", "NPV Base", "NPV Optimistic")
    wsR.Range(wsR.Cells(r, 1), wsR.Cells(r + nSites - 1, 1)).Value = _
        wsS.Range(wsS.Cells(FIRST_SITE_ROW, 1), wsS.Cells(lastSite, 1)).Value
    wsR.Range(wsR.Cells(r, 2), wsR.Cells(r + nSites - 1, 2)).Value = _
        wsS.Range(wsS.Cells(FIRST_SITE_ROW, 2), wsS.Cells(lastSite, 2)).Value
    wsR.Range(wsR.Cells(r, 3), wsR.Cells(r + nSites - 1, 3)).Value = _
        wsS.Range(wsS.Cells(FIRST_SITE_ROW, 5), wsS.Cells(lastSite, 5)).Value

    For i = 0 To 2
        wsA.Range(SCENARIO_CELL).Value = scenarios(i)
        Application.CalculateFull

        wsR.Cells(5, 2 + i).Value = wsC.Range(NPV_CELL).Value
        wsR.Cells(6, 2 + i).Value = wsC.Range(IRR_CELL).Value
        wsR.Cells(7, 2 + i).Value = wsC.Range(PAYBACK_CELL).Value

        positive = 0
        total = 0
        For r = FIRST_SITE_ROW To lastSite
            siteNpv = wsS.Cells(r, SITE_NPV_COL).Value
            total = total + siteNpv
            If siteNpv > 0 Then positive = positive + 1
            wsR.Cells(12 + r - FIRST_SITE_ROW, 4 + i).Value = siteNpv
        Next r
        wsR.Cells(8, 2 + i).Value = positive & " of " & nSites
        wsR.Cells(9, 2 + i).Value = total
    Next i

    FormatResults wsR, nSites

Restore:
    wsA.Range(SCENARIO_CELL).Value = original
    Application.CalculateFull
    Application.ScreenUpdating = True
    If Err.Number <> 0 Then
        MsgBox "RunAllScenarios stopped: " & Err.Description, vbExclamation
    Else
        wsR.Activate
        MsgBox "Scenario comparison written to '" & RESULTS_SHEET & "'.", vbInformation
    End If
End Sub

Public Sub BreakEvenPrice()
Attribute BreakEvenPrice.VB_ProcData.VB_Invoke_Func = "B\n14"
    Dim wsA As Worksheet, wsC As Worksheet, priceCell As Range
    Dim keyRow As Variant, original As Double, breakEven As Double

    Set wsA = ThisWorkbook.Worksheets("Assumptions")
    Set wsC = ThisWorkbook.Worksheets("Cash flow")

    keyRow = Application.Match("price", wsA.Columns(1), 0)
    If IsError(keyRow) Then
        MsgBox "Could not find the 'price' input on the Assumptions sheet.", vbExclamation
        Exit Sub
    End If

    ' Column C/D/E hold Pessimistic/Base/Optimistic; D3 is the selected index (1-3)
    Set priceCell = wsA.Cells(keyRow, 2 + wsA.Range(SCENARIO_INDEX_CELL).Value)
    original = priceCell.Value

    On Error GoTo Restore
    If wsC.Range(NPV_CELL).GoalSeek(Goal:=0, ChangingCell:=priceCell) Then
        breakEven = priceCell.Value
    End If

Restore:
    priceCell.Value = original
    Application.CalculateFull
    If Err.Number <> 0 Or breakEven = 0 Then
        MsgBox "Goal Seek did not find a break-even price.", vbExclamation
    Else
        MsgBox "Scenario " & wsA.Range(SCENARIO_CELL).Value & ": NPV = 0 at a net price of EUR " & _
            Format(breakEven, "0.000") & "/kWh (input " & Format(original, "0.00") & ").", vbInformation
    End If
End Sub

Private Function ResultsSheet() As Worksheet
    On Error Resume Next
    Set ResultsSheet = ThisWorkbook.Worksheets(RESULTS_SHEET)
    On Error GoTo 0
    If ResultsSheet Is Nothing Then
        Set ResultsSheet = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets(ThisWorkbook.Worksheets.Count))
        ResultsSheet.Name = RESULTS_SHEET
    End If
End Function

Private Sub FormatResults(ws As Worksheet, nSites As Long)
    With ws
        .Range("A4:D4, A11:F11").Font.Bold = True
        .Range("A4:D4, A11:F11").Interior.Color = RGB(30, 58, 95)
        .Range("A4:D4, A11:F11").Font.Color = RGB(255, 255, 255)
        .Range("B5:D5, B9:D9").NumberFormat = "#,##0;[Red]-#,##0"
        .Range("B6:D6").NumberFormat = "0.0%"
        .Range(.Cells(12, 4), .Cells(11 + nSites, 6)).NumberFormat = "#,##0;[Red]-#,##0"
        .Columns("A").ColumnWidth = 34
        .Columns("B").ColumnWidth = 28
        .Columns("C:F").ColumnWidth = 18
    End With
End Sub
