; Stop a running OMNA (and its local service) before files are replaced or removed.
!macro customInit
  IfFileExists "$INSTDIR\OMNA.exe" 0 +3
    nsExec::Exec '"$INSTDIR\OMNA.exe" --quit'
    Sleep 3000
!macroend

!macro customUnInit
  IfFileExists "$INSTDIR\OMNA.exe" 0 +3
    nsExec::Exec '"$INSTDIR\OMNA.exe" --quit'
    Sleep 3000
!macroend
