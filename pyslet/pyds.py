#! /usr/bin/env python
"""This module provides a simple implementation of an EntitySet using a python list object."""


import pyslet.mc_csdl as edm
import pyslet.mc_edmx as edmx
import pyslet.odatav2 as odata
import pyslet.xml20081126.structures as xml
import pyslet.xmlnames20091208 as xmlns
import pyslet.iso8601 as iso8601
import pyslet.rfc2616 as http

import string, hashlib


class InMemoryEntityStore(object):
	"""Implements an in-memory entity set using a python dictionary.
	
	Each entity is stored as a tuple of values in the order in which the
	properties of that entity type are declared.  Complex values are
	stored as nested tuples.
	
	Media streams are simply strings stored in a parallel dictionary
	mapping keys on to a tuple of media-type and string."""
	
	def __init__(self,entitySet=None):
		self.data={}				#: simple dictionary of the values
		self.nextKey=None
		self.streams={}				#: simple dictionary of streams
		self.delHooks=[]			#: list of functions to call during deletion
		self.entitySet=entitySet	#: the entity set we're bound to
		if entitySet is not None:
			self.BindToEntitySet(entitySet)
			
	def BindToEntitySet(self,entitySet):
		"""Binds this entity store to the given entity set."""
		entitySet.Bind(EntityCollection,entityStore=self)
		self.entitySet=entitySet
		
	def AddEntity(self,e):
		key=e.Key()
		value=[]
		for pName in e.DataKeys():
			if not e.Selected(pName):
				continue
			p=e[pName]
			if isinstance(p,edm.Complex):
				value.append(self.GetTupleFromComplex(p))
			elif isinstance(p,edm.SimpleValue):
				value.append(p.pyValue)
		self.data[key]=tuple(value)
		# At this point the entity exists
		e.exists=True

	def UpdateEntity(self,e):
		# e is an EntityTypeInstance, we need to convert it to a tuple
		key=e.Key()
		value=list(self.data[key])
		i=0
		for pName in e.DataKeys():
			if e.Selected(pName):
				p=e[pName]
				if isinstance(p,edm.Complex):
					value[i]=self.GetTupleFromComplex(p)
				elif isinstance(p,edm.SimpleValue):
					value[i]=p.pyValue
			i=i+1
		self.data[key]=tuple(value)

	def GetTupleFromComplex(self,complexValue):
		value=[]
		for pName in complexValue.iterkeys():
			p=complexValue[pName]
			if isinstance(p,edm.Complex):
				value.append(self.GetTupleFromComplex(p))
			else:
				value.append(p.pyValue)
		return tuple(value)

	def DeleteEntity(self,key):
		key=self.entitySet.GetKey(key)
		for hook in self.delHooks:
			hook(key)
		del self.data[key]
		if key in self.streams:
			del self.streams[key]
	
	def AddDeleteHook(self,delHook):
		"""Adds a function to call during entity deletion."""
		self.delHooks.append(delHook)

	def ResetDeleteHooks(self):
		"""We use this method to clear the delete hook lists."""
		self.delHooks=[]

	def NextKey(self):
		"""In the special case where the key is an integer, return the next free integer"""
		if self.nextKey is None:
			kps=list(self.entitySet.KeyKeys())
			if len(kps)!=1:
				raise KeyError("Can't get next value of compound key")
			key=self.entitySet.entityType[kps[0]]()
			if not isinstance(key,edm.NumericValue):
				raise KeyError("Can't get next value non-integer key")
			keys=self.data.keys()
			if keys:
				keys.sort()
				self.nextKey=keys[-1]
			else:
				self.nextKey=key.SetToZero()
		while self.nextKey in self.data:
			self.nextKey+=1
		return self.nextKey


class InMemoryAssociationIndex(object):
	"""An in memory index that implements the association between two sets of entities.
	
	Instances of this class create storage for an association between
	*fromEntityStore* and *toEntityStore* which are
	:py:class:`InMemoryEntityStore` instances.
		
	If *propertyName* (and optionally *reverseName*) is provided then
	the index is immediately bound to the data service, see
	:py:meth:`Bind` for more information."""	
	def __init__(self,fromEntityStore,toEntityStore,propertyName=None,reverseName=None):
		self.index={}			#: a dictionary mapping source keys on to sets of target keys
		self.reverseIndex={}	#: the reverse index mapping target keys on to sets of source keys
		self.fromEntityStore=fromEntityStore
		fromEntityStore.AddDeleteHook(self.DeleteHook)
		self.toEntityStore=toEntityStore
		toEntityStore.AddDeleteHook(self.ReverseDeleteHook)
		if propertyName is not None:
			self.Bind(propertyName,reverseName)
			
	def Bind(self,propertyName,reverseName=None):
		"""Binds this index to the named property of the entity set
		bound to :py:attr:`fromEntityStore`.
		
		If the association is reversible *reverseName* can also be used
		to bind that property in the entity set bound to
		:py:attr:`toEntityStore`"""
		self.fromEntityStore.entitySet.BindNavigation(propertyName,NavigationEntityCollection,associationIndex=self,reverse=False)
		if self.reverseIndex is not None and reverseName is not None:
			self.toEntityStore.entitySet.BindNavigation(reverseName,NavigationEntityCollection,associationIndex=self,reverse=True)
		
# 	def Navigate(self,fromKey):
# 		"""We keep a simple index dictionary mapping source keys to target keys.
# 		
# 		We always use dictionaries of target keys as the values in the
# 		index for simplicity allowing us to use the same representation
# 		for single and multiple cardinality associations."""
# 		if self.IsEntityCollection():
# 			return NavigationEntityCollection(self,fromKey)
# 		else:
# 			result=self.index.get(fromKey,None)
# 			if result is None:
# 				return None
# 			elif len(result)==1:
# 				k=result.keys()[0]
# 				entity=self.otherEnd.entitySet.OpenCollection()[k]
# 				return entity
# 			else:
# 				raise KeyError("Navigation error, found multiple entities")
	
	def AddLink(self,fromKey,toKey):
		"""Adds a link from *fromKey* to *toKey*"""
		self.index.setdefault(fromKey,set()).add(toKey)
		self.reverseIndex.setdefault(toKey,set()).add(fromKey)

	def RemoveLink(self,fromKey,toKey):
		"""Removes a link from *fromKey* to *toKey*"""
		self.index.get(fromKey,set()).pop(toKey)
		self.reverseIndex.get(toKey,set()).pop(fromKey)
		
	def DeleteHook(self,fromKey):
		"""Called when a key from the source entity set is being deleted."""
		try:
			toKeys=self.index[fromKey]
			for toKey in toKeys:
				fromKeys=self.reverseIndex[toKey]
				fromKeys.remove(fromKey)
				if len(fromKeys)==0:
					del self.reverseIndex[toKey]
			del self.index[fromKey]			
		except KeyError:
			pass

	def ReverseDeleteHook(self,toKey):
		"""Called when a key from the target entity set is being deleted."""
		try:
			fromKeys=self.reverseIndex[toKey]
			for fromKey in fromKeys:
				toKeys=self.index[fromKey]
				toKeys.remove(toKey)
				if len(toKeys)==0:
					del self.index[fromKey]
			del self.reverseIndex[toKey]			
		except KeyError:
			pass


class Entity(odata.Entity):
	"""We override OData's EntitySet class to support the
	media-streaming methods."""	

	def __init__(self,entitySet,entityStore):
		super(Entity,self).__init__(entitySet)
		self.entityStore=entityStore		#: points to the entity storage
			
	def GetStreamType(self):
		"""Returns the content type of the entity's media stream.
		
		Must return a :py:class:`pyslet.rfc2616.MediaType` instance."""
		key=self.Key()
		if key in self.entityStore.streams:
			type,stream=self.entityStore.streams[key]
			return type
		else:
			return http.MediaType('application/octet-stream')
			
	def GetStreamSize(self):
		"""Returns the size of the entity's media stream in bytes."""
		key=self.Key()
		if key in self.entityStore.streams:
			type,stream=self.entityStore.streams[key]
			return len(stream)
		else:
			return 0
		
	def GetStreamGenerator(self):
		"""A generator function that yields blocks (strings) of data from the entity's media stream."""
		key=self.Key()
		if key in self.entityStore.streams:
			type,stream=self.entityStore.streams[key]
			yield stream
		else:
			yield ''

	def SetStreamFromGenerator(self,streamType,src):
		"""Replaces the contents of this stream with the strings output by iterating over src.
		
		If the entity has a concurrency token and it is a binary value,
		updates the token to be a hash of the stream."""
		etag=self.ETag()
		if isinstance(etag,edm.BinaryValue):
			h=hashlib.sha256()
		else:
			h=None
		value=[]
		for data in src:
			value.append(data)
		data=string.join(data,'')
		self.SetItemStream(streamType,data)
		if h is not None:
			h.update(data)
			etag.SetFromPyValue(h.digest())
			self.Update()
				
	def SetItemStream(self,streamType,stream):
		key=self.Key()
		self.entityStore.streams[key]=(streamType,stream)

	

class EntityCollection(odata.EntityCollection):
	"""An entity collection that provides access to entities stored in
	the :py:class:`InMemoryEntitySet` *entityStore*."""
	
	def __init__(self,entitySet,entityStore):
		super(EntityCollection,self).__init__(entitySet)
		self.entityStore=entityStore
		
	def NewEntity(self,autoKey=False):
		"""Returns an OData aware instance"""
		e=Entity(self.entitySet,self.entityStore)	
		if autoKey:
			e.SetKey(self.entityStore.NextKey())
		return e 
	
	def __len__(self):
		return len(self.entityStore.data)

	def entityGenerator(self):
		for value in self.entityStore.data.itervalues():
			e=Entity(self.entitySet,self.entityStore)
			for pName,pValue in zip(e.DataKeys(),value):
				p=e[pName]
				if isinstance(p,edm.Complex):
					self.SetComplexFromTuple(p,pValue)
				else:
					p.SetFromPyValue(pValue)
			e.exists=True
			yield e
		
	def itervalues(self):
		return self.OrderEntities(
			self.ExpandEntities(
			self.FilterEntities(
			self.entityGenerator())))
		
	def __getitem__(self,key):
		e=Entity(self.entitySet,self.entityStore)
		for pName,pValue in zip(e.DataKeys(),self.entityStore.data[key]):
			p=e[pName]
			if isinstance(p,edm.Complex):
				self.SetComplexFromTuple(p,pValue)
			else:
				p.pyValue=pValue
		e.exists=True
		if self.CheckFilter(e):
			e.Expand(self.expand,self.select)
			return e
		else:
			raise KeyError

	def SetComplexFromTuple(self,complexValue,t):
		for pName,pValue in zip(complexValue.iterkeys(),t):
			p=complexValue[pName]
			if isinstance(p,edm.Complex):
				self.SetComplexFromTuple(p,pValue)
			else:
				p.SetFromPyValue(pValue)

	def InsertEntity(self,entity):
		try:
			key=entity.Key()
		except KeyError:
			# if the entity doesn't have a key, autogenerate one
			key=self.entityStore.NextKey()
			entity.SetKey(key)
		if key in self:
			raise KeyError("%s already exists"%odata.ODataURI.FormatEntityKey(entity))
		# now process any bindings
		self.entityStore.AddEntity(entity)
		self.UpdateBindings(entity)
		
	def UpdateEntity(self,entity):
		key=entity.Key()
		self.entityStore.UpdateEntity(entity)
		# now process any bindings
		self.UpdateBindings(entity)
		
	def __delitem__(self,key):
		self.entityStore.DeleteEntity(key)
	
	
class NavigationEntityCollection(odata.NavigationEntityCollection):
	
	def __init__(self,name,fromEntity,toEntitySet,associationIndex,reverse):
		self.associationIndex=associationIndex
		self.reverse=reverse
		if self.reverse:
			self.index=self.associationIndex.reverseIndex
		else:
			self.index=self.associationIndex.index
		super(NavigationEntityCollection,self).__init__(name,fromEntity,toEntitySet)
		self.collection=self.entitySet.OpenCollection()
		self.key=self.fromEntity.Key()
	
	def NewEntity(self,autoKey=False):
		"""Returns an OData aware instance"""
		return self.collection.NewEntity(autoKey)	
	
	def close(self):
		if self.collection is not None:
			self.collection.close()
			self.collection=None
						
	def __len__(self):
		resultSet=self.index.get(self.key,set())
		return len(resultSet)

	def entityGenerator(self):
		# we create a collection from the appropriate entity set first
		resultSet=self.index.get(self.key,set())
		for k in resultSet:
			yield self.collection[k]
		
	def itervalues(self):
		return self.OrderEntities(
			self.ExpandEntities(
			self.FilterEntities(
			self.entityGenerator())))

	def __setitem__(self,key,value):
		resultSet=self.index.get(self.key,set())
		if key in resultSet:
			# no operation
			return
		# forces a check of value to ensure it is good
		self.collection[key]=value
		if not self.fromEntity.IsEntityCollection(self.name):
			# replace whatever we have - harder to do
			for k in resultSet:
				if self.reverse:
					self.associationIndex.RemoveLink(key,self.key)
				else:
					self.associationIndex.RemoveLink(self.key,key)
		# just add this one to the index
		if self.reverse:
			self.associationIndex.AddLink(key,self.key)
		else:
			self.associationIndex.AddLink(self.key,key)