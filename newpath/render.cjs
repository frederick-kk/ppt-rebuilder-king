/* Local native-object backend. Input is a validated, independent scene. */
'use strict';
const fs = require('fs');
const pptxgen = require('pptxgenjs');
const JSZip = require('jszip');

const PX_PER_INCH = 144;
const EMU_PER_PX = 6350;
const inch = value => value / PX_PER_INCH;
const pt = value => value / 2;
const dash = value => ({solid: 'solid', dash: 'dash', dot: 'sysDot'}[value || 'solid']);
const bounds = box => ({x: inch(box[0]), y: inch(box[1]), w: inch(box[2]), h: inch(box[3])});
const emu = value => Math.round(value * EMU_PER_PX);

function addText(slide, item, counts) {
  const options = {
    ...bounds(item.box), objectName: item.id, isTextBox: true,
    fontFace: item.font || 'Microsoft YaHei', fontSize: pt(item.size),
    color: item.color, bold: !!item.bold, italic: !!item.italic, underline: !!item.underline,
    align: item.align || 'left', valign: 'top', margin: 0,
    breakLine: false, wrap: false, fit: 'none',
    paraSpaceAfter: 0, paraSpaceBefore: 0, charSpacing: pt(item.tracking || 0),
    rotate: ((item.rotation || 0) % 360 + 360) % 360,
    vert: item.vertical ? 'eaVert' : 'horz', lang: 'zh-CN',
  };
  const text = item.runs ? item.runs.map(run => ({
    text: run.text,
    options: {fontFace: options.fontFace, fontSize: pt(run.size === undefined ? item.size : run.size),
      color: run.color === undefined ? item.color : run.color,
      bold: run.bold === undefined ? !!item.bold : run.bold,
      italic: run.italic === undefined ? !!item.italic : run.italic,
      underline: run.underline === undefined ? !!item.underline : run.underline,
      breakLine: false, charSpacing: pt(item.tracking || 0)},
  })) : item.text;
  slide.addText(text, options);
  counts.text_frames++;
}

function addLine(slide, item, counts) {
  const [x1, y1, x2, y2] = item.points;
  slide.addShape('line', {
    objectName: item.id, x: inch(Math.min(x1, x2)), y: inch(Math.min(y1, y2)),
    w: inch(Math.abs(x2 - x1)), h: inch(Math.abs(y2 - y1)),
    flipH: x1 > x2, flipV: y1 > y2,
    line: {color: item.stroke, width: pt(item.stroke_width), dashType: dash(item.dash),
      beginArrowType: item.start_arrow || 'none', endArrowType: item.end_arrow || 'none'},
  });
  counts.lines++;
}

function addTable(slide, table, counts) {
  const [left, top, width, height] = table.box;
  let y = top;
  for (let row = 0; row < table.rows.length; row++) {
    let x = left;
    for (let col = 0; col < table.rows[row].length; col++) {
      const cellBox = [x, y, table.column_widths[col], table.row_heights[row]];
      slide.addShape('rect', {objectName: `${table.id}::cell.${row}.${col}`,
        ...bounds(cellBox), fill: table.fill ? {color: table.fill} : {color: 'FFFFFF', transparency: 100},
        line: {color: 'FFFFFF', transparency: 100, width: 0}});
      counts.table_cells++;
      x += table.column_widths[col];
    }
    y += table.row_heights[row];
  }
  for (const child of table._cell_lines) addText(slide, child, counts);
  let x = left;
  for (let col = 0; col <= table.column_widths.length; col++) {
    addLine(slide, {id: `${table.id}::grid.v.${col}`, points: [x, top, x, top + height],
      stroke: table.gridcolor, stroke_width: table.grid_width}, counts);
    if (col < table.column_widths.length) x += table.column_widths[col];
  }
  y = top;
  for (let row = 0; row <= table.row_heights.length; row++) {
    addLine(slide, {id: `${table.id}::grid.h.${row}`, points: [left, y, left + width, y],
      stroke: table.gridcolor, stroke_width: table.grid_width}, counts);
    if (row < table.row_heights.length) y += table.row_heights[row];
  }
  counts.table_groups++;
}

function groupTables(xml, tables) {
  let nextId = 1;
  for (const match of xml.matchAll(/<p:cNvPr\b[^>]*\bid="(\d+)"/g)) nextId = Math.max(nextId, Number(match[1]) + 1);
  for (const table of tables) {
    const pieces = [...xml.matchAll(/<p:sp>[\s\S]*?<\/p:sp>/g)]
      .filter(match => {
        const name = match[0].match(/<p:cNvPr\b[^>]*\bname="([^"]*)"/);
        return name && name[1].startsWith(table.id + '::');
      });
    if (!pieces.length) throw new Error(`empty table composite: ${table.id}`);
    const children = pieces.map(match => match[0]).join('');
    const start = pieces[0].index;
    const end = pieces[pieces.length - 1].index + pieces[pieces.length - 1][0].length;
    if (xml.slice(start, end) !== children) throw new Error(`noncontiguous table composite: ${table.id}`);
    const [x, y, w, h] = table.box.map(emu);
    const group = `<p:grpSp><p:nvGrpSpPr><p:cNvPr id="${nextId++}" name="${table.id}"/>` +
      `<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm>` +
      `<a:off x="${x}" y="${y}"/><a:ext cx="${w}" cy="${h}"/>` +
      `<a:chOff x="${x}" y="${y}"/><a:chExt cx="${w}" cy="${h}"/>` +
      `</a:xfrm></p:grpSpPr>${children}</p:grpSp>`;
    xml = xml.slice(0, start) + group + xml.slice(end);
  }
  return xml;
}

async function main() {
  if (process.argv.length !== 3) throw new Error('usage: node render.cjs render.json');
  const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const pptx = new pptxgen();
  pptx.defineLayout({name: 'NEWPATH_1920_1080', width: inch(1920), height: inch(1080)});
  pptx.layout = 'NEWPATH_1920_1080';
  pptx.author = 'Newpath';
  pptx.subject = 'Independent native slide objects';
  pptx.title = 'Newpath';
  pptx.company = 'Newpath';
  pptx.lang = 'zh-CN';
  pptx.theme = {headFontFace: 'Microsoft YaHei', bodyFontFace: 'Microsoft YaHei', lang: 'zh-CN'};
  const slide = pptx.addSlide();
  slide.background = {path: request.background};
  const counts = {text_frames: 0, shapes: 0, lines: 0, table_groups: 0, table_cells: 0, foreground_images: 0};
  for (const item of request.items) {
    if (item.kind === 'text' || item.kind === 'formula') addText(slide, item, counts);
    else if (item.kind === 'line') addLine(slide, item, counts);
    else if (item.kind === 'shape') {
      slide.addShape(item.shape, {objectName: item.id, ...bounds(item.box),
        rotate: ((item.rotation || 0) % 360 + 360) % 360,
        fill: item.fill ? {color: item.fill, transparency: (1 - item.opacity) * 100} : {color: 'FFFFFF', transparency: 100},
        line: item.stroke ? {color: item.stroke, width: pt(item.stroke_width), dashType: dash(item.dash), transparency: (1 - item.opacity) * 100} : {color: 'FFFFFF', transparency: 100, width: 0}});
      counts.shapes++;
    } else if (item.kind === 'art') {
      slide.addImage({objectName: item.id, path: item.path, ...bounds(item.target)});
      counts.foreground_images++;
    } else if (item.kind === 'table') addTable(slide, item, counts);
    else throw new Error(`unsupported item kind: ${item.kind}`);
  }
  const bytes = await pptx.write({outputType: 'nodebuffer', compression: true});
  const zip = await JSZip.loadAsync(bytes);
  let xml = await zip.file('ppt/slides/slide1.xml').async('string');
  xml = groupTables(xml, request.items.filter(item => item.kind === 'table'));
  zip.file('ppt/slides/slide1.xml', xml);
  fs.writeFileSync(request.output, await zip.generateAsync({type: 'nodebuffer', compression: 'DEFLATE'}));
  process.stdout.write(JSON.stringify({native_counts: counts}));
}

main().catch(error => {
  process.stderr.write(error && error.message ? error.message : 'PPTX assembly failed');
  process.exitCode = 1;
});
