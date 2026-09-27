/**
 * Live: the production CSP, enforced by a real browser on the page nginx
 * actually serves (docs/V3_PLAN.md Phase 2; E2-V4's render half).
 * scripts/verify_e2.py reads the header; these tests show the browser applies
 * it. No questions are asked here, so no model calls are spent.
 */

import { expect, test } from '@playwright/test'

import { API, signIn, watchCsp } from './helpers'

function directives(csp: string): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const part of csp.split(';')) {
    const [name, ...values] = part.trim().split(/\s+/)
    if (name) out.set(name, values)
  }
  return out
}

test('the served CSP allows scripts from the app only, and never eval', async ({ request }) => {
  const response = await request.get('/')
  expect(response.ok()).toBe(true)
  const csp = response.headers()['content-security-policy'] ?? ''
  const policy = directives(csp)
  expect(policy.get('script-src')).toEqual(["'self'"])
  expect(policy.get('connect-src')).toEqual(["'self'", new URL(API).origin])
  expect(csp).not.toContain('unsafe-eval')
})

test('the browser refuses eval and the Function constructor on the served page', async ({ page }) => {
  await page.goto('/')
  // Code evaluated BY Playwright is exempt from the CSP's eval ban (the
  // DevTools protocol allows it by default), which would make this test pass
  // or fail for the wrong reason. So the attempt runs in a later task, as the
  // page's own code, where the policy applies.
  const outcome = await page.evaluate(
    () =>
      new Promise<Record<string, string>>((resolve) => {
        setTimeout(() => {
          const attempt = (run: () => unknown): string => {
            try {
              run()
              return 'ran'
            } catch (e) {
              return (e as Error).name
            }
          }
          resolve({
            // eslint-disable-next-line no-eval -- the point of the test: the page must not be able to eval
            eval: attempt(() => eval('1 + 1')),
            // eslint-disable-next-line no-new-func -- likewise for the Function constructor
            newFunction: attempt(() => new Function('return 1')()),
          })
        }, 0)
      }),
  )
  expect(outcome).toEqual({ eval: 'EvalError', newFunction: 'EvalError' })
})

test('signing in and opening the dashboard raise no CSP violation', async ({ page }) => {
  const violations = await watchCsp(page)
  await signIn(page)
  await page.getByRole('button', { name: 'Dashboard', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
  expect(await violations()).toEqual([])
})

test('the violation watcher itself sees a violation when one happens', async ({ page }) => {
  // Guards the test above against passing vacuously with a deaf watcher.
  const violations = await watchCsp(page)
  await page.goto('/')
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        setTimeout(() => {
          try {
            // eslint-disable-next-line no-eval -- deliberately provoke the policy
            eval('1 + 1')
          } catch {
            // refused, as intended
          }
          resolve()
        }, 0)
      }),
  )
  await expect.poll(violations).not.toEqual([])
})
