const vscode = require('vscode');
function activate(context) {
  for (const [command, args] of [['start', []], ['resume', ['--resume']]]) {
    context.subscriptions.push(vscode.commands.registerCommand('barrassons.' + command, async () => {
      const folders = vscode.workspace.workspaceFolders || [];
      const folder = folders.length === 1 ? folders[0] : await vscode.window.showWorkspaceFolderPick();
      if (!folder) return;
      const terminal = vscode.window.createTerminal({name:'Barrassons',cwd:folder.uri.fsPath,shellPath:'barrassons',shellArgs:args});
      terminal.show();
    }));
  }
}
module.exports = {activate};
