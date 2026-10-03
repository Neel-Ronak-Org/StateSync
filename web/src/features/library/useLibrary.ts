import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'

const DOCUMENT_POLL_MS = 3000

/** Library folders (only fetched when signed in), with create, rename and delete. */
export function useFolders() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['library-folders'],
    queryFn: () => api.libraryFolders(),
    staleTime: 10_000,
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['library-folders'] })
    void queryClient.invalidateQueries({ queryKey: ['workspace-folders'] })
  }
  const create = useMutation({ mutationFn: (name: string) => api.createFolder(name), onSuccess: refresh })
  const rename = useMutation({
    mutationFn: (v: { folderId: string; name: string }) => api.renameFolder(v.folderId, v.name),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (folderId: string) => api.deleteFolder(folderId),
    onSuccess: (_, folderId) => {
      queryClient.removeQueries({ queryKey: ['library-documents', folderId] })
      refresh()
    },
  })
  return {
    folders: query.data?.folders ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    createFolder: create.mutateAsync,
    renameFolder: rename.mutateAsync,
    deleteFolder: remove.mutateAsync,
  }
}

/** A folder's documents; refetched every 3 s while any is still processing. */
export function useDocuments(folderId: string | null) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['library-documents', folderId],
    queryFn: () => api.libraryDocuments(folderId!),
    enabled: Boolean(folderId),
    refetchInterval: (q) =>
      q.state.data?.documents.some((d) => d.status === 'processing') ? DOCUMENT_POLL_MS : false,
  })
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['library-documents', folderId] })
    void queryClient.invalidateQueries({ queryKey: ['library-folders'] })
  }
  const remove = useMutation({ mutationFn: (id: string) => api.deleteDocument(id), onSuccess: refresh })
  return {
    documents: query.data?.documents ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    refresh,
    deleteDocument: remove.mutateAsync,
  }
}

/** Which folders a workspace can search. */
export function useWorkspaceFolders(workspaceId: string) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['workspace-folders', workspaceId],
    queryFn: () => api.workspaceFolders(workspaceId),
  })
  const save = useMutation({
    mutationFn: (ids: string[]) => api.setWorkspaceFolders(workspaceId, ids),
    onSuccess: (res) => {
      queryClient.setQueryData(['workspace-folders', workspaceId], res)
      void queryClient.invalidateQueries({ queryKey: ['library-folders'] })
    },
  })
  return {
    folderIds: query.data?.folder_ids ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    save: save.mutateAsync,
  }
}
