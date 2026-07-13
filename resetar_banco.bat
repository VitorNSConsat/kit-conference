@echo off
chcp 65001 >nul
echo.
echo =====================================================
echo   RESETAR BANCO DE DADOS - Kit Conference
echo =====================================================
echo.
echo ATENCAO: Esta acao apaga TODOS os dados do sistema.
echo (kits, sessoes, itens, templates, usuarios)
echo.
set /p confirma="Digite SIM para confirmar: "
if /i not "%confirma%"=="SIM" (
    echo Operacao cancelada.
    pause
    exit /b 0
)

echo.
echo Parando a aplicacao (Python)...
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Apagando banco de dados...
if exist kit_conference.db (
    del /F kit_conference.db
    echo Banco apagado com sucesso!
) else (
    echo Arquivo kit_conference.db nao encontrado.
)

echo.
echo Pronto! Reinicie a aplicacao com:
echo   python main.py
echo   ou o script de inicializacao que voce usar normalmente.
echo.
pause
