export function validateTurn(turn) {
  if (!turn.usage || !turn.items.some(item => item.type === 'agent_message')) {
    throw new Error('incomplete_turn');
  }
  const knownNotice = 'Skill descriptions were shortened to fit the skills context budget. Codex can still see every skill, but some descriptions are shorter. Disable unused skills or plugins to leave more room for the rest.';
  for (const item of turn.items) {
    if (['agent_message', 'reasoning'].includes(item.type)) continue;
    if (item.type === 'error' && item.message === knownNotice) continue;
    throw new Error('unexpected_item');
  }
  return JSON.parse(turn.finalResponse);
}
