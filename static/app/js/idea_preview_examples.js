(() => {
  'use strict';
  const data = document.getElementById('idea-preview-examples');
  const container = document.getElementById('ideaPreviewExamples');
  if (!data || !container) return;
  for (const sample of JSON.parse(data.textContent)) {
    const preview = globalThis.AI2AIIdeaPreviews.create(sample);
    if (preview) container.append(preview);
  }
})();
