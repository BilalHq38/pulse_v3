import { BACKEND_BASE_URL, resolveMediaUrl } from './backend-url';

test('resolveMediaUrl keeps absolute and data URLs unchanged', () => {
  expect(resolveMediaUrl('https://cdn.example.com/a.png')).toBe('https://cdn.example.com/a.png');
  expect(resolveMediaUrl('data:image/png;base64,abc')).toBe('data:image/png;base64,abc');
});

test('resolveMediaUrl prefixes backend origin for API media paths', () => {
  expect(resolveMediaUrl('/api/products/media/company-1/image.png')).toBe(
    `${BACKEND_BASE_URL}/api/products/media/company-1/image.png`,
  );
});
