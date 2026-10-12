(function(root){
  "use strict";
  function createChart(chart){
    if(!chart || !Array.isArray(chart.labels) || !Array.isArray(chart.datasets)){
      return null;
    }
    const colors = ['#3b82f6','#f97316','#10b981','#a855f7','#ef4444','#eab308','#06b6d4','#ec4899','#64748b','#84cc16','#8b5cf6','#14b8a6'];
    const svgNamespace = 'http://www.w3.org/2000/svg';
    const card = document.createElement('article');
    card.className = 'bara-chart-card';
    const heading = document.createElement('h3');
    heading.textContent = chart.title || 'Graphique';
    const figure = document.createElement('figure');
    const svg = document.createElementNS(svgNamespace, 'svg');
    svg.setAttribute('viewBox', '0 0 760 360');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', heading.textContent);
    const svgTitle = document.createElementNS(svgNamespace, 'title');
    svgTitle.textContent = heading.textContent;
    svg.appendChild(svgTitle);
    const legend = document.createElement('div');
    legend.className = 'chart-legend';

    function addSvg(name, attributes, parent=svg){
      const element = document.createElementNS(svgNamespace, name);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
      parent.appendChild(element);
      return element;
    }
    function addText(text, x, y, attributes={}){
      const element = addSvg('text', {x, y, fill:'currentColor', 'font-size':11, ...attributes});
      element.textContent = text;
      return element;
    }
    function addLegend(label, color){
      const item = document.createElement('span');
      item.className = 'chart-legend-item';
      const swatch = document.createElement('span');
      swatch.className = 'chart-legend-swatch';
      swatch.style.backgroundColor = color;
      const text = document.createElement('span');
      text.textContent = label;
      item.append(swatch, text);
      legend.appendChild(item);
    }

    if(chart.chart_type === 'pie'){
      const labels = chart.labels;
      const values = chart.datasets[0].values;
      const total = values.reduce((sum, value) => sum + value, 0);
      let angle = -Math.PI / 2;
      labels.forEach((label, index) => {
        const slice = values[index] / total * Math.PI * 2;
        const nextAngle = angle + slice;
        const startX = 380 + Math.cos(angle) * 125;
        const startY = 175 + Math.sin(angle) * 125;
        const endX = 380 + Math.cos(nextAngle) * 125;
        const endY = 175 + Math.sin(nextAngle) * 125;
        const largeArc = slice > Math.PI ? 1 : 0;
        const color = colors[index % colors.length];
        const sliceShape = values[index] === total
          ? addSvg('circle', {cx:380, cy:175, r:125, fill:color, stroke:'var(--panel)', 'stroke-width':2})
          : addSvg('path', {
              d:`M 380 175 L ${startX} ${startY} A 125 125 0 ${largeArc} 1 ${endX} ${endY} Z`,
              fill:color,
              stroke:'var(--panel)',
              'stroke-width':2
            });
        const tip = document.createElementNS(svgNamespace, 'title');
        tip.textContent = `${label} : ${values[index].toLocaleString('fr-FR')}`;
        sliceShape.appendChild(tip);
        addLegend(`${label} — ${values[index].toLocaleString('fr-FR')}`, color);
        angle = nextAngle;
      });
    } else {
      const width = 760, height = 360;
      const margin = {top:24, right:20, bottom:58, left:68};
      const plotWidth = width - margin.left - margin.right;
      const plotHeight = height - margin.top - margin.bottom;
      const allValues = chart.datasets.flatMap(dataset => dataset.values);
      let minValue = Math.min(0, ...allValues);
      let maxValue = Math.max(0, ...allValues);
      if(minValue === maxValue) maxValue = minValue + 1;
      const yFor = value => margin.top + (maxValue - value) / (maxValue - minValue) * plotHeight;

      for(let tick = 0; tick <= 4; tick++){
        const value = minValue + (maxValue - minValue) * tick / 4;
        const y = yFor(value);
        addSvg('line', {x1:margin.left, y1:y, x2:width-margin.right, y2:y, stroke:'currentColor', 'stroke-opacity':0.14});
        addText(value.toLocaleString('fr-FR', {maximumFractionDigits:2}), margin.left-8, y+4, {'text-anchor':'end'});
      }
      addSvg('line', {x1:margin.left, y1:margin.top, x2:margin.left, y2:height-margin.bottom, stroke:'currentColor', 'stroke-opacity':0.45});
      addSvg('line', {x1:margin.left, y1:yFor(0), x2:width-margin.right, y2:yFor(0), stroke:'currentColor', 'stroke-opacity':0.45});

      const categoryWidth = plotWidth / chart.labels.length;
      chart.labels.forEach((label, index) => {
        addText(label.length > 15 ? `${label.slice(0,14)}…` : label, margin.left+(index+0.5)*categoryWidth, height-26, {'text-anchor':'middle'})
          .appendChild(Object.assign(document.createElementNS(svgNamespace, 'title'), {textContent:label}));
      });

      chart.datasets.forEach((dataset, datasetIndex) => {
        const color = colors[datasetIndex % colors.length];
        addLegend(dataset.label, color);
        if(chart.chart_type === 'bar'){
          const groupWidth = categoryWidth * 0.76;
          const barWidth = groupWidth / chart.datasets.length;
          dataset.values.forEach((value, index) => {
            const x = margin.left + index*categoryWidth + (categoryWidth-groupWidth)/2 + datasetIndex*barWidth;
            const y = yFor(value);
            const bar = addSvg('rect', {
              x, y:Math.min(y, yFor(0)), width:Math.max(1, barWidth-2),
              height:Math.max(1, Math.abs(y-yFor(0))), fill:color, rx:2
            });
            const tip = document.createElementNS(svgNamespace, 'title');
            tip.textContent = `${chart.labels[index]} — ${dataset.label} : ${value.toLocaleString('fr-FR')}`;
            bar.appendChild(tip);
          });
        } else {
          const points = dataset.values.map((value, index) => ({
            x:margin.left+(index+0.5)*categoryWidth,
            y:yFor(value),
            value
          }));
          addSvg('polyline', {
            points:points.map(point => `${point.x},${point.y}`).join(' '),
            fill:'none', stroke:color, 'stroke-width':3, 'stroke-linejoin':'round', 'stroke-linecap':'round'
          });
          points.forEach((point, index) => {
            const marker = addSvg('circle', {cx:point.x, cy:point.y, r:4, fill:color, stroke:'var(--panel)', 'stroke-width':2});
            const tip = document.createElementNS(svgNamespace, 'title');
            tip.textContent = `${chart.labels[index]} — ${dataset.label} : ${point.value.toLocaleString('fr-FR')}`;
            marker.appendChild(tip);
          });
        }
      });
    }

    const table = document.createElement('table');
    table.className = 'chart-data-table';
    const caption = document.createElement('caption');
    caption.textContent = `Données du graphique : ${heading.textContent}`;
    table.appendChild(caption);
    const tableHead = document.createElement('tr');
    const categoryHeader = document.createElement('th');
    categoryHeader.scope = 'col';
    categoryHeader.textContent = 'Catégorie';
    tableHead.appendChild(categoryHeader);
    chart.datasets.forEach(dataset => {
      const header = document.createElement('th');
      header.scope = 'col';
      header.textContent = dataset.label;
      tableHead.appendChild(header);
    });
    const tableHeadSection = document.createElement('thead');
    tableHeadSection.appendChild(tableHead);
    table.appendChild(tableHeadSection);
    const tableBody = document.createElement('tbody');
    chart.labels.forEach((label, labelIndex) => {
      const row = document.createElement('tr');
      const rowHeader = document.createElement('th');
      rowHeader.scope = 'row';
      rowHeader.textContent = label;
      row.appendChild(rowHeader);
      chart.datasets.forEach(dataset => {
        const cell = document.createElement('td');
        cell.textContent = dataset.values[labelIndex].toLocaleString('fr-FR');
        row.appendChild(cell);
      });
      tableBody.appendChild(row);
    });
    table.appendChild(tableBody);
    figure.append(svg, table);
    card.append(heading, figure, legend);
    return card;
  }


  root.AI2AICharts = {createChart};
})(globalThis);
