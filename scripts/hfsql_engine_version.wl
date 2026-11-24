// WINDEV project initialization, without an analysis or a first window.
// Argument 1 is a reader connection JSON file. Argument 2 is the result path.
sConfigPath is string = CommandLine(1)
sResultPath is string = CommandLine(2)
IF sConfigPath = "" OR sResultPath = "" THEN EndProgram()

sConfig is string = fLoadText(sConfigPath)
IF sConfig = "" THEN EndProgram()
vConfig is Variant = JSONToVariant(sConfig)

cnx is Connection
cnx..Provider = hAccessHFClientServer
cnx..Server = StringBuild("%1:%2", vConfig.host, vConfig.port)
cnx..Database = vConfig.database
cnx..User = vConfig.user
cnx..Password = vConfig.password
cnx..Access = hORead
IF NOT HOpenConnection(cnx) THEN EndProgram()

sVersion is string = HInfoServer(cnx, hInfoVI)
HCloseConnection(cnx)
IF sVersion = "" THEN EndProgram()

vResult is Variant
vResult.serverVersion = sVersion
fSaveText(sResultPath, VariantToJSON(vResult))
EndProgram()
