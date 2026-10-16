'use strict';

const vscode = require('vscode');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const core = require('./core');

const API_KEY_SECRET = 'barrassons.apiKey';
const CHAT_PARTICIPANT_ID = 'ai2ai.barrassons.chat';

let missingKeyNoticeShown = false;
let lastInlineErrorAt = 0;
let outputChannel;

function readCliConfig() {
  try {
    const root = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
    const value = JSON.parse(fs.readFileSync(path.join(root, 'barrassons', 'config.json'), 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function configuredApiBase(configuration, cliConfig = readCliConfig()) {
  const inspected = configuration.inspect('apiBase');
  const configured = inspected && (
    inspected.workspaceFolderValue || inspected.workspaceValue || inspected.globalValue
  );
  const candidate = configured || process.env.BARRASSONS_API_BASE || cliConfig.api_base
    || configuration.get('apiBase', core.DEFAULT_API_BASE);
  return core.normalizeApiBase(candidate);
}

async function getCredentials(context) {
  const configuration = vscode.workspace.getConfiguration('barrassons');
  const cliConfig = readCliConfig();
  const apiBase = configuredApiBase(configuration, cliConfig);
  const storedKey = await context.secrets.get(API_KEY_SECRET);
  const cliKey = typeof cliConfig.api_key === 'string' && cliConfig.api_key.startsWith('sk-ai2ai-')
    ? cliConfig.api_key : '';
  const apiKey = storedKey || cliKey;
  if (!apiKey) throw new Error('Clé API manquante. Lance `barrassons login` ou utilise « Barrassons : configurer la clé API ».');
  return {apiBase, apiKey};
}

async function configureApiKey(context) {
  const configuration = vscode.workspace.getConfiguration('barrassons');
  let apiBase;
  try {
    apiBase = configuredApiBase(configuration);
  } catch (error) {
    vscode.window.showErrorMessage(error.message);
    return;
  }
  const baseInput = await vscode.window.showInputBox({
    title: 'Barrassons · serveur AI2AI',
    prompt: 'Adresse de l’API (les clés API ne doivent jamais être incluses dans cette URL).',
    value: apiBase,
    ignoreFocusOut: true
  });
  if (!baseInput) return;
  try {
    apiBase = core.normalizeApiBase(baseInput);
  } catch (error) {
    vscode.window.showErrorMessage(error.message);
    return;
  }

  const apiKey = await vscode.window.showInputBox({
    title: 'Barrassons · clé API',
    prompt: 'Colle ta clé AI2AI. Elle sera vérifiée puis stockée dans le coffre sécurisé de VS Code.',
    password: true,
    ignoreFocusOut: true
  });
  if (!apiKey?.trim()) return;

  try {
    const result = await core.getModels({apiBase, apiKey: apiKey.trim()});
    if (!Array.isArray(result?.data)) throw new Error('Le serveur n’a pas renvoyé de catalogue de modèles valide.');
    await context.secrets.store(API_KEY_SECRET, apiKey.trim());
    await configuration.update('apiBase', apiBase, vscode.ConfigurationTarget.Global);
    missingKeyNoticeShown = false;
    vscode.window.showInformationMessage('Connexion Barrassons vérifiée et enregistrée dans VS Code.');
  } catch (error) {
    vscode.window.showErrorMessage(`Connexion Barrassons impossible : ${error.message}`);
  }
}

async function selectModel(context) {
  try {
    const credentials = await getCredentials(context);
    const result = await core.getModels(credentials);
    const rows = [{label: 'Auto 2AI', description: 'Choix automatique d’un modèle compatible', modelId: 'auto'}];
    const seenModels = new Set(['auto']);
    for (const model of result?.data || []) {
      if (typeof model?.id === 'string' && !seenModels.has(model.id)) {
        seenModels.add(model.id);
        rows.push({label: model.id, description: model.owned_by || 'Modèle AI2AI', modelId: model.id});
      }
    }
    const selected = await vscode.window.showQuickPick(rows, {
      placeHolder: 'Choisir le modèle utilisé par le chat et les suggestions inline'
    });
    if (!selected) return;
    await vscode.workspace.getConfiguration('barrassons').update('model', selected.modelId, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(`Modèle Barrassons : ${selected.label}`);
  } catch (error) {
    vscode.window.showErrorMessage(`Barrassons : ${error.message}`);
  }
}

function waitForDelay(milliseconds, token) {
  if (token.isCancellationRequested) return Promise.resolve(false);
  if (milliseconds <= 0) return Promise.resolve(true);
  return new Promise(resolve => {
    let subscription;
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      subscription?.dispose();
      resolve(value);
    };
    const timer = setTimeout(() => finish(true), milliseconds);
    subscription = token.onCancellationRequested(() => finish(false));
    if (token.isCancellationRequested) finish(false);
  });
}

function trimInlineContext(document, position, maximum) {
  const lineStart = Math.max(0, position.line - 32);
  const lineEnd = Math.min(document.lineCount - 1, position.line + 12);
  const start = new vscode.Position(lineStart, 0);
  const end = document.lineAt(lineEnd).range.end;
  const before = document.getText(new vscode.Range(start, position));
  const after = document.getText(new vscode.Range(position, end));
  return core.boundedContext(before, after, maximum);
}

function inlineCacheKey(document, position, apiBase, model) {
  return [document.uri.toString(), document.version, position.line, position.character, apiBase, model].join('|');
}

function makeInlineProvider(context) {
  const cache = new Map();
  return {
    async provideInlineCompletionItems(document, position, completionContext, token) {
      const configuration = vscode.workspace.getConfiguration('barrassons');
      if (!configuration.get('inlineCompletions.enabled', false)
        || document.uri.scheme !== 'file'
        || document.languageId === 'plaintext'
        || core.isSensitiveFile(vscode.workspace.asRelativePath(document.uri, false))
        || completionContext.selectedCompletionInfo
        || token.isCancellationRequested) return [];

      const debounce = Math.max(0, Math.min(2000, configuration.get('inlineCompletions.debounceMs', 350)));
      if (!await waitForDelay(debounce, token) || token.isCancellationRequested) return [];

      let credentials;
      try {
        credentials = await getCredentials(context);
      } catch (error) {
        if (!missingKeyNoticeShown) {
          missingKeyNoticeShown = true;
          const action = await vscode.window.showWarningMessage(error.message, 'Configurer');
          if (action) await vscode.commands.executeCommand('barrassons.configure');
        }
        return [];
      }
      if (token.isCancellationRequested) return [];

      const model = configuration.get('model', 'auto') || 'auto';
      const key = inlineCacheKey(document, position, credentials.apiBase, model);
      const cached = cache.get(key);
      if (cached && Date.now() - cached.at < 60_000) {
        return [new vscode.InlineCompletionItem(cached.text, new vscode.Range(position, position))];
      }

      const maxContextChars = configuration.get('inlineCompletions.maxContextChars', 4500);
      const contextAtCursor = trimInlineContext(document, position, maxContextChars);
      const relativePath = vscode.workspace.asRelativePath(document.uri, false);
      const messages = core.buildInlineMessages({
        language: document.languageId,
        fileName: relativePath,
        ...contextAtCursor,
        maxContextChars
      });
      const controller = new AbortController();
      const subscription = token.onCancellationRequested(() => controller.abort());
      try {
        const result = await core.requestCompletion({
          ...credentials,
          model,
          messages,
          maxTokens: Math.max(32, Math.min(192, configuration.get('inlineCompletions.maxTokens', 96))),
          temperature: 0.15,
          signal: controller.signal
        });
        if (token.isCancellationRequested) return [];
        const text = core.extractInlineCompletion(result);
        if (!text || !text.trim()) return [];
        cache.set(key, {text, at: Date.now()});
        while (cache.size > 80) cache.delete(cache.keys().next().value);
        return [new vscode.InlineCompletionItem(text, new vscode.Range(position, position))];
      } catch (error) {
        if (error?.name === 'AbortError') return [];
        if (/Clé API refusée/i.test(error.message) && !missingKeyNoticeShown) {
          missingKeyNoticeShown = true;
          const action = await vscode.window.showErrorMessage('La clé API Barrassons a été refusée.', 'Configurer');
          if (action) await vscode.commands.executeCommand('barrassons.configure');
        } else if (Date.now() - lastInlineErrorAt > 30_000) {
          lastInlineErrorAt = Date.now();
          outputChannel.appendLine(`Suggestion inline indisponible : ${error.message}`);
        }
        return [];
      } finally {
        subscription.dispose();
      }
    }
  };
}

function sensitiveFile(relativePath) {
  return core.isSensitiveFile(relativePath);
}

function selectedEditorContext() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) return [];
  const relativePath = vscode.workspace.asRelativePath(editor.document.uri, false);
  if (sensitiveFile(relativePath)) return [];
  const text = editor.document.getText(editor.selection);
  return text ? [{label: `${relativePath} · sélection`, text: text.slice(0, 12_000)}] : [];
}

function uriAndRange(value) {
  if (value && typeof value.scheme === 'string' && typeof value.fsPath === 'string') return {uri: value};
  if (value?.uri && typeof value.uri.scheme === 'string') return {uri: value.uri, range: value.range};
  return {};
}

async function referencedFileContext(request, existingContext, token) {
  const contextBlocks = [...existingContext];
  const seen = new Set();
  for (const item of contextBlocks) {
    const filename = item.label.split(' · ')[0];
    if (filename) seen.add(filename);
  }
  let remaining = 20_000 - contextBlocks.reduce((total, item) => total + item.text.length, 0);
  for (const reference of (request.references || []).slice(0, 4)) {
    if (token.isCancellationRequested || remaining <= 0) break;
    const {uri, range} = uriAndRange(reference.value);
    if (!uri || uri.scheme !== 'file') continue;
    const folder = vscode.workspace.getWorkspaceFolder(uri);
    if (!folder) continue;
    const relativePath = vscode.workspace.asRelativePath(uri, false);
    if (seen.has(relativePath) || sensitiveFile(relativePath)) continue;
    try {
      const document = await vscode.workspace.openTextDocument(uri);
      const text = document.getText(range);
      if (!text) continue;
      const excerpt = text.slice(0, Math.min(8_000, remaining));
      contextBlocks.push({label: relativePath, text: excerpt});
      seen.add(relativePath);
      remaining -= excerpt.length;
    } catch {
      // Un fichier binaire ou indisponible reste hors du contexte envoyé au modèle.
    }
  }
  return contextBlocks;
}

function chatHistory(context) {
  const messages = [];
  for (const turn of context.history || []) {
    if (turn instanceof vscode.ChatRequestTurn && turn.prompt) {
      messages.push({role: 'user', content: turn.prompt});
    } else if (turn instanceof vscode.ChatResponseTurn) {
      const text = (turn.response || [])
        .filter(part => part instanceof vscode.ChatResponseMarkdownPart)
        .map(part => part.value?.value || '')
        .join('');
      if (text) messages.push({role: 'assistant', content: text});
    }
  }
  return messages;
}

async function handleChatRequest(request, chatContext, response, token, extensionContext) {
  if (token.isCancellationRequested) return;
  try {
    const credentials = await getCredentials(extensionContext);
    const historyReferences = (chatContext.history || [])
      .filter(turn => turn instanceof vscode.ChatRequestTurn)
      .slice(-4)
      .flatMap(turn => turn.references || []);
    const contexts = await referencedFileContext({
      ...request,
      references: [...(request.references || []), ...historyReferences]
    }, selectedEditorContext(), token);
    if (token.isCancellationRequested) return;
    const configuration = vscode.workspace.getConfiguration('barrassons');
    const model = configuration.get('model', 'auto') || 'auto';
    const messages = core.buildChatMessages({
      history: chatHistory(chatContext),
      prompt: request.prompt,
      command: request.command,
      context: contexts
    });
    response.progress('Barrassons prépare une réponse…');
    let received = false;
    const writer = core.createTextBatcher(text => response.markdown(text), {
      isCancelled: () => token.isCancellationRequested
    });
    const cancellation = cancellationSignal(token);
    const maxTokens = Math.max(128, Math.min(4096, configuration.get('chat.maxTokens', 1400)));
    try {
      try {
        await core.streamCompletion({
          ...credentials,
          model,
          messages,
          maxTokens,
          temperature: 0.25,
          signal: cancellation.signal,
          onDelta: text => {
            if (token.isCancellationRequested) return;
            received = true;
            writer.push(text);
          }
        });
      } catch (error) {
        if (token.isCancellationRequested || received
          || !/Streaming is not supported for this model yet/i.test(error.message)) throw error;
        response.progress('Ce modèle ne prend pas en charge le streaming ; Barrassons attend sa réponse…');
        const result = await core.requestCompletion({
          ...credentials, model, messages, maxTokens, temperature: 0.25, signal: cancellation.signal
        });
        const text = result?.choices?.[0]?.message?.content;
        if (typeof text === 'string' && text) {
          received = true;
          writer.push(text);
        }
      }
    } finally {
      try {
        writer.dispose();
      } finally {
        cancellation.dispose();
      }
    }
    if (!received && !token.isCancellationRequested) response.markdown('Aucun contenu n’a été renvoyé par le modèle.');
    return {metadata: {model}};
  } catch (error) {
    if (token.isCancellationRequested || error?.name === 'AbortError') return;
    response.markdown(`\n\n⚠️ **Barrassons :** ${escapeMarkdown(String(error.message || error).slice(0, 500))}`);
    return {errorDetails: {message: String(error.message || error).slice(0, 500)}};
  }
}

function cancellationSignal(token) {
  const controller = new AbortController();
  const subscription = token.onCancellationRequested(() => controller.abort());
  if (token.isCancellationRequested) controller.abort();
  return {signal: controller.signal, dispose: () => subscription.dispose()};
}

function escapeMarkdown(text) {
  return String(text).replace(/[\\`*_{}\[\]()#+\-.!|>]/g, '\\$&').replace(/[\r\n]+/g, ' ');
}

async function toggleInlineCompletions() {
  const configuration = vscode.workspace.getConfiguration('barrassons');
  const enabled = configuration.get('inlineCompletions.enabled', false);
  if (enabled) {
    await configuration.update('inlineCompletions.enabled', false, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage('Les suggestions inline de Barrassons sont désactivées.');
    return;
  }
  const choice = await vscode.window.showWarningMessage(
    'Les suggestions inline envoient le contexte autour du curseur à l’API AI2AI. Chaque requête consomme les crédits du compte.',
    'Activer', 'Annuler'
  );
  if (choice !== 'Activer') return;
  await configuration.update('inlineCompletions.enabled', true, vscode.ConfigurationTarget.Global);
  vscode.window.showInformationMessage('Suggestions inline Barrassons activées. Utilise Alt+\\ pour demander une suggestion.');
}

async function openChat() {
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', {query: '@barrassons '});
  } catch {
    vscode.window.showInformationMessage('Ouvre la vue Chat, puis écris @barrassons suivi de ta demande.');
  }
}

async function chooseWorkspaceFolder() {
  const folders = vscode.workspace.workspaceFolders || [];
  return folders.length === 1 ? folders[0] : vscode.window.showWorkspaceFolderPick();
}

function activate(context) {
  outputChannel = vscode.window.createOutputChannel('Barrassons');
  context.subscriptions.push(outputChannel);

  const commands = [
    vscode.commands.registerCommand('barrassons.start', async () => {
      const folder = await chooseWorkspaceFolder();
      if (!folder) return;
      const terminal = vscode.window.createTerminal({name: 'Barrassons', cwd: folder.uri.fsPath});
      terminal.show();
      terminal.sendText('barrassons');
    }),
    vscode.commands.registerCommand('barrassons.resume', async () => {
      const folder = await chooseWorkspaceFolder();
      if (!folder) return;
      const terminal = vscode.window.createTerminal({name: 'Barrassons', cwd: folder.uri.fsPath});
      terminal.show();
      terminal.sendText('barrassons --resume');
    }),
    vscode.commands.registerCommand('barrassons.configure', () => configureApiKey(context)),
    vscode.commands.registerCommand('barrassons.selectModel', () => selectModel(context)),
    vscode.commands.registerCommand('barrassons.toggleInlineCompletions', toggleInlineCompletions),
    vscode.commands.registerCommand('barrassons.openChat', openChat)
  ];
  context.subscriptions.push(...commands);

  context.subscriptions.push(vscode.languages.registerInlineCompletionItemProvider(
    {scheme: 'file'}, makeInlineProvider(context)
  ));

  const participant = vscode.chat.createChatParticipant(
    CHAT_PARTICIPANT_ID,
    (request, chatContext, response, token) => handleChatRequest(request, chatContext, response, token, context)
  );
  participant.iconPath = new vscode.ThemeIcon('sparkle');
  context.subscriptions.push(participant);
}

function deactivate() {}

module.exports = {
  activate,
  deactivate,
  readCliConfig,
  configuredApiBase,
  getCredentials,
  trimInlineContext,
  sensitiveFile,
  makeInlineProvider,
  chatHistory,
  referencedFileContext,
  handleChatRequest
};
