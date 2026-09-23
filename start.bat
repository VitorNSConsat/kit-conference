@echo off
rem PORTAL_SERVICE_KEY: mesma chave nos tres servicos (Kit, Resolucoes, Portal)
rem -- habilita o painel Pessoas do Portal gerenciar usuarios daqui. Sem ela,
rem tudo o mais continua funcionando normalmente, so o painel fica
rem indisponivel. Este .bat nao grava a chave (nao e gerado por instalador
rem nenhum, diferente da Resolucoes/Portal) -- defina PORTAL_SERVICE_KEY como
rem variavel de ambiente do Windows (Painel de Controle -> Sistema ->
rem Variaveis de Ambiente) e ela chega aqui sozinha.
echo Iniciando Conferencia de Kits...
cd /d "%~dp0"
python run.py
pause
