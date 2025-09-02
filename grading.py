def commits_substring_count(resp: list, needle: str):
	"""Counts commits with substring in message. Accepts parsed JSON from response to github api"""
	i=0
	for commit in resp:
		i += needle in commit['commit']['message']
	return i

def issues_substring_count(resp: list, needle: str):
	"""Counts issues with substring in title. Accepts parsed JSON from response to github api"""
	i=0
	for issue in resp:
		i += needle in issue['title']
	return i

def commits_local_count(resp: list):
	"""Counts commits made w/o using github web. Accepts parsed JSON from response to github api"""
	i=0
	for commit in resp:
		if not (commit['commit']['committer']['email'] == 'noreply@github.com' and commit['commit']['verification']['verified']):
			i += 1
	return i