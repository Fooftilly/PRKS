import { describe, expect, it } from 'vitest'
import { snapshotModalFields } from './snapshot'

describe('create form snapshot', () => {
  it('records text, checks, and files the way the classic serializer does', () => {
    const modal = document.createElement('div')
    modal.innerHTML = [
      '<input id="title" type="text" value="Ada">',
      '<input id="pick" type="checkbox">',
      '<input id="file" type="file">',
      '<textarea id="note">hello</textarea>',
    ].join('')
    const box = modal.querySelector('#pick') as HTMLInputElement
    box.checked = true
    const file = modal.querySelector('#file') as HTMLInputElement
    const blob = new File(['abc'], 'paper.pdf', { lastModified: 5 })
    const list = {
      0: blob,
      length: 1,
      item: () => blob,
    }
    Object.defineProperty(file, 'files', { value: list })
    const raw = snapshotModalFields(modal)
    expect(raw).toContain('input:text:title')
    expect(raw).toContain('Ada')
    expect(raw).toContain('input:checkbox:pick')
    expect(raw).toContain('hello')
    expect(raw).toContain('paper.pdf:3:5')
  })
})
