import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const app = fs.readFileSync(path.join(root, 'src/renderer/App.vue'), 'utf8')
const stage = fs.readFileSync(path.join(root, 'src/renderer/components/Live2DStage.vue'), 'utf8')

assert.match(app, /<Live2DStage\s+:key=/, 'App must mount Live2DStage during cold start')
assert.doesNotMatch(app, /<Live2DStage[^>]*v-if="customModelPath"/, 'Stage must not depend on load_model for existence')
assert.match(stage, /props\.modelPath \|\| ['"]\/live2d\/sakiko\/live2D_model\/3\.model\.json['"]/, 'Stage must retain its bootstrap model')
assert.match(app, /command\?\.type === ['"]load_model['"]/, 'Python load_model must remain able to override bootstrap model')

console.log('Electron cold-start bootstrap checks passed.')
