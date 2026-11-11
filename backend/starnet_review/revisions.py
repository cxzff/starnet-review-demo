"""Apply a chosen local replacement to a copy of the source; keep all original package parts."""
import hashlib
from io import BytesIO
from lxml import etree as ET
from zipfile import ZipFile

from .core import DATA, flatten, import_files, key, save, split_document

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
ET.register_namespace('w', W[1:-1])


def replace_text(element, old, new):
    nodes = list(element.iter(W + 't'))
    text = ''.join(n.text or '' for n in nodes)
    if text.count(old) != 1:
        raise ValueError('此位置的摘录不能唯一定位，请编辑原文后重新评审')
    start = text.index(old)
    end = start + len(old)
    cursor = 0
    inserted = False
    for node in nodes:
        value = node.text or ''
        right = cursor + len(value)
        if cursor < end and right > start:
            prefix = value[:max(0, start - cursor)]
            suffix = value[max(0, end - cursor):] if end < right else ''
            node.text = prefix + (new if not inserted else '') + suffix
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            inserted = True
        cursor = right


def patch_docx(data, block, quote, replacement):
    with ZipFile(BytesIO(data)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        body = root.find(W + 'body')
        element = body[block['location']['start'] - 1]
        if block['row']:
            row = element.findall(W + 'tr')[block['row'] - 1]
            cells = [c for c in row.findall(W + 'tc') if quote in ''.join(n.text or '' for n in c.iter(W + 't'))]
            if len(cells) != 1:
                raise ValueError('此建议跨越表格单元格，请在建议中选择单个单元格的文字')
            element = cells[0]
        replace_text(element, quote, replacement)
        output = BytesIO()
        with ZipFile(output, 'w') as target:
            for info in archive.infolist():
                target.writestr(info, ET.tostring(root, encoding='utf-8', xml_declaration=True)
                                if info.filename == 'word/document.xml' else archive.read(info.filename))
        return output.getvalue()


def accept(project, finding_id, replacement, category=None):
    before_snapshot = project['graph']['snapshot_id']
    finding = next(f for f in project['findings'] if f['id'] == finding_id)
    if finding['status'] != 'open':
        raise ValueError('此意见已经处理或需要重新评审')
    doc = next(d for d in project['documents'] if d['id'] == finding['document_id'])
    block = next(b for b in doc['blocks'] if b['id'] == finding['block_id'])
    if doc['sha256'] != finding['source_sha256'] or block['text'] != finding['original_block']:
        raise ValueError('原文版本已变化，请重新评审此处')
    if not replacement.strip():
        raise ValueError('请填写可采纳的修改文字')
    directory = DATA / project['id']
    source = directory / doc['source_file']
    revision_id = key()
    destination = directory / f'{revision_id}{source.suffix}'
    if source.suffix == '.docx':
        destination.write_bytes(patch_docx(source.read_bytes(), block, finding['quote'], replacement))
    else:
        lines = source.read_text().splitlines(keepends=True)
        start, end = block['location']['start'] - 1, block['location']['end']
        part = ''.join(lines[start:end])
        if part.count(finding['quote']) != 1:
            raise ValueError('原文摘录不能唯一定位')
        lines[start:end] = [part.replace(finding['quote'], replacement, 1)]
        destination.write_text(''.join(lines))
    old_file = doc['source_file']
    old_docs = [d for d in project['documents'] if d['source_file'] == old_file]
    source_id = doc['id'].split('-')[0]
    new_trees = split_document(destination, source_id, doc['type'])
    new_docs = [flatten(t, doc['filename']) for t in new_trees]
    remap = {}
    for old, new in zip(old_docs, new_docs, strict=True):
        new['title'] = old['title']
        new['source_file'] = destination.name
        for before, after in zip(old['blocks'], new['blocks'], strict=True):
            remap[before['id']] = after['id']
        project['documents'][project['documents'].index(old)] = new
    finding['status'] = 'accepted'
    if category and category != finding['category']:
        finding['original_category'] = finding['category']
        finding['category'] = category
    finding['applied_replacement'] = replacement
    finding['revision_id'] = revision_id
    for f in project['findings']:
        if f['block_id'] in remap:
            f['block_id'] = remap[f['block_id']]
            current_doc = next(d for d in new_docs if d['id'] == f['document_id'])
            current_block = next(b for b in current_doc['blocks'] if b['id'] == f['block_id'])
            if f['status'] == 'open':
                if current_block['text'] == f['original_block']:
                    f['source_sha256'] = current_doc['sha256']
                else:
                    f['status'] = 'outdated'
    project['revisions'].append({'id': revision_id, 'finding_id': finding_id, 'source': old_file,
                                  'file': destination.name, 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                                  'before_snapshot': before_snapshot,
                                  'quote': finding['quote'], 'replacement': replacement})
    # Rebuild a new ClauseGraph snapshot from the revised source. Old snapshots remain immutable.
    import_files([], project)
    project['revisions'][-1]['after_snapshot'] = project['graph']['snapshot_id']
    save(project)
    return project
