import test from 'node:test';
import assert from 'node:assert/strict';
import { validateTurn } from './result.mjs';

const notice = 'Skill descriptions were shortened to fit the skills context budget. Codex can still see every skill, but some descriptions are shorter. Disable unused skills or plugins to leave more room for the rest.';
const response = JSON.stringify({replies:['我先确认。','请明确范围。','稍后同步进度。']});
const turn = (items) => ({items, finalResponse:response, usage:{input_tokens:20,output_tokens:15}});
test('known skill catalog notice does not discard a completed valid reply', () => {
  assert.equal(validateTurn(turn([{type:'error',message:notice},{type:'agent_message',text:response}])).replies.length,3);
});
test('unknown error remains a failure', () => {
  assert.throws(() => validateTurn(turn([{type:'error',message:'authentication failed'}])));
});
test('actual tool items remain rejected even with a valid final response', () => {
  for (const type of ['command_execution','file_change','mcp_tool_call','web_search','future_tool']) {
    assert.throws(() => validateTurn(turn([{type}])));
  }
});
test('missing completion is never a successful result', () => {
  assert.throws(() => validateTurn({...turn([{type:'agent_message',text:response}]),usage:null}));
});
test('missing final message is rejected', () => {
  assert.throws(() => validateTurn(turn([{type:'reasoning'}])));
});
