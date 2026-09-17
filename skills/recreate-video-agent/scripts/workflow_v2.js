// Appended to the versioned n8n Planner node. V1 remains unchanged.
function validateBlueprintV2(bp, duration) {
  const fail = message => { throw new Error(message); };
  const number = x => typeof x === 'number' && Number.isFinite(x);
  const roster = (items, key) => {
    if (!Array.isArray(items)) fail(key + '清单必须是数组');
    const map = new Map();
    for (const item of items) {
      const id = item?.[key];
      if (typeof id !== 'string' || !id.trim() || map.has(id)) fail(key + '缺失或重复');
      map.set(id, item);
    }
    return map;
  };
  const ids = (list, known, name) => {
    if (!Array.isArray(list) || new Set(list).size !== list.length || list.some(x => typeof x !== 'string' || !known.has(x))) fail(name + '无效或引用不存在的ID');
    return list;
  };
  if (bp.schemaVersion !== '7.0') fail('V2要求Blueprint 7.0');
  const people = roster(bp['视频元素']?.['人物'], 'characterId');
  const products = roster(bp['视频元素']?.['产品'], 'productId');
  const voices = roster(bp['声音结构']?.voiceProfiles, 'speakerId');
  for (const voice of voices.values()) {
    if (voice.characterId != null && !people.has(voice.characterId)) fail('声源关联不存在的人物');
  }
  const shots = bp['逐镜头拆解'];
  roster(shots, 'shotId');
  if (!shots.length) fail('缺少逐镜头拆解');
  let cursor = 0;
  const seenPeople = new Set(), seenProducts = new Set(), utterances = new Set();
  for (const shot of shots) {
    const {start:a, end:b} = shot;
    if (!number(a) || !number(b) || b <= a || Math.abs(a-cursor) > .05) fail('镜头时间无效或存在空档/重叠');
    cursor = b;
    ids(shot.visibleCharacterIds, people, 'visibleCharacterIds').forEach(x => seenPeople.add(x));
    ids(shot.productIds, products, 'productIds').forEach(x => seenProducts.add(x));
    if (!Array.isArray(shot.keyframes) || !shot.keyframes.length) fail('缺少keyframes');
    for (const frame of shot.keyframes) if (!number(frame.time) || frame.time < a || frame.time >= b) fail('keyframe越界');
    if (!Array.isArray(shot['声音']?.utterances)) fail('utterances必须是数组');
    for (const line of shot['声音'].utterances) {
      if (typeof line.utteranceId !== 'string' || !line.utteranceId || utterances.has(line.utteranceId) || !voices.has(line.speakerId)) fail('utteranceId重复/缺失或speakerId不存在');
      utterances.add(line.utteranceId);
      if (!number(line.start) || !number(line.end) || !(a <= line.start && line.start < line.end && line.end <= b)) fail('对白时间越界');
    }
  }
  if (!number(duration) || Math.abs(cursor-duration) > .1) fail('蓝图未覆盖目标时长');
  if (people.size !== seenPeople.size || products.size !== seenProducts.size) fail('实体清单与镜头引用不一致');
  for (const [id, person] of people) {
    const t = person.referenceTime;
    if (!number(t) || !shots.some(s => s.start <= t && t < s.end && s.visibleCharacterIds.includes(id))) fail('人物referenceTime未落在其出镜镜头');
  }
  const cuts = bp['节奏结构']?.cutPlan;
  if (!Array.isArray(cuts) || cuts.length !== shots.length-1) fail('cutPlan未覆盖相邻镜头');
  cuts.forEach((c,i) => {
    if (!number(c.time) || Math.abs(c.time-shots[i+1].start) > .05 || c.fromShotId !== shots[i].shotId || c.toShotId !== shots[i+1].shotId) fail('cutPlan边界引用错误');
  });
}

function plannerV2(bp, config, basePolicy) {
  validateBlueprintV2(bp, config.targetDuration);
  // The engine and duration policy are unchanged; only V2 evidence selection changes.
  const policy = JSON.parse(JSON.stringify(basePolicy));
  policy.blueprintSchemaVersion = '7.0';
  policy.policyVersion = '2.0.0';
  const working = JSON.parse(JSON.stringify(bp));
  const originalShots = bp['逐镜头拆解'];
  const people = bp['视频元素']['人物'];
  for (const shot of working['逐镜头拆解']) {
    for (const person of people) {
      if (shot.visibleCharacterIds.includes(person.characterId) && shot.start <= person.referenceTime && person.referenceTime < shot.end) {
        const frame = shot.keyframes.find(f => Math.abs(f.time-person.referenceTime) < .001);
        if (frame) frame.priority = Math.max(5, Number(frame.priority || 1));
        else shot.keyframes.push({time:person.referenceTime, priority:5, reason:'人物清晰出现定位'});
      }
    }
  }
  const plan = plannerV1(working, config, policy);
  plan.plannerVersion = '2';
  plan.policyVersion = '2.0.0';
  // diagnose V1 assumes a voice/person bijection; that diagnostic is invalid in V2.
  plan.blueprintWarnings = plan.blueprintWarnings.filter(x => x !== '声线未绑定人物');
  plan.reviewIssues = Array.isArray(bp.reviewIssues) ? bp.reviewIssues : [];
  for (const segment of plan.segments) {
    const shots = originalShots.filter(s => s.start < segment.globalEnd && s.end > segment.globalStart);
    segment.shotIds = shots.map(s => s.shotId);
    segment.sourceCharacterIds = [...new Set(shots.flatMap(s => s.visibleCharacterIds))];
    segment.sourceProductIds = [...new Set(shots.flatMap(s => s.productIds))];
    for (const anchor of segment.anchors) {
      const shot = originalShots.find(s => s.start <= anchor.timestamp && anchor.timestamp < s.end);
      if (!shot || anchor.timestamp < 0 || anchor.timestamp >= Number(plan.sourceDuration)) throw new Error('anchor无真实镜头或超出原片');
      anchor.selectionReason = ['boundary','coverage'].includes(anchor.shotId) ? anchor.shotId : 'keyframe';
      anchor.shotId = shot.shotId;
      // Shot-wide membership is not per-frame observed visibility.
      anchor.shotCharacterIds = shot.visibleCharacterIds;
      anchor.shotProductIds = shot.productIds;
    }
  }
  return plan;
}
