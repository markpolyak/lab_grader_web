def commits_substring_count(resp: list, needle: str):
	i=0
	for commit in resp:
		i += needle in commit['commit']['message']
	return i

def issues_substring_count(resp: list, needle: str):
	i=0
	for issue in resp:
		i += needle in issue['title']
	return i

def commits_local_count(resp: list):
	i=0
	for commit in resp:
		if not (commit['commit']['committer']['email'] == 'noreply@github.com' and commit['commit']['verification']['verified']):
			i += 1
	return i